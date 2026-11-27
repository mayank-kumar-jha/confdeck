import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from confdeck.conversation import plan
from confdeck.models import Camera
from confdeck.tracking import TrackingSettings, Zone


def room():
    return [Zone(id=seat, name=seat, azimuth=i*50, elevation=30, presets=mapping)
            for i, (seat, mapping) in enumerate([
                ('a', {'one':'21'}), ('b', {'one':'22'}),
                ('c', {'two':'21'}), ('d', {'two':'22'}),
                ('presenter', {'three':'21'})])]


@pytest.mark.parametrize('seats,available,expected', [
    (['a'], {'one','two','three'}, ['a']),
    (['a','c'], {'one','two','three'}, ['a','c']),
    (['b','a'], {'one','two','three'}, ['b']),
    (['a','b','c'], {'one','two','three'}, ['a','c']),
    (['presenter','a','c'], {'one','two','three'}, ['presenter','a','c']),
    (['c','presenter','a','b'], {'one','two','three'}, ['c','presenter','a']),
    (['a','c'], {'two'}, ['c']),
    (['a'], set(), []),
])
def test_camera_coverage(seats, available, expected):
    result = plan(room(), seats, available, presenter_seat='presenter', presenter_camera='three')
    assert list(result) == expected
    assert len({c for c, _ in result.values()}) == len(result)


def test_prefers_current_framing_and_reserves_presenter_camera():
    zones = [SimpleNamespace(id='a',presets={'one':'21','two':'21','three':'22'}),
             SimpleNamespace(id='p',presets={'three':'21','one':'22'})]
    assert plan(zones,['a'],{'one','two'}, {'two':('a','21')}) == {'a':('two','21')}
    assert plan(zones,['a','p'],{'one','three'}, presenter_seat='p',presenter_camera='three') == {'a':('one','21'),'p':('three','21')}


def setup(runtime):
    runtime.settings.tracking = TrackingSettings(conversation_layout=True, settle_ms=0, zones=room(), presenter_seat='presenter', presenter_camera='three')
    runtime.settings.cameras = [Camera(id=cid, ip=f'127.0.0.{i+1}', control_enabled=True, control_protocol='visca') for i,cid in enumerate(['one','two','three'])]
    follow=runtime.follow
    follow.configure(runtime.settings.tracking)
    follow.live=True
    follow.recall=AsyncMock(return_value={'ok':True})
    return follow


async def lock(follow, seat):
    zone=next(z for z in follow.runtime.settings.tracking.zones if z.id==seat)
    follow.dispatch({'zone':seat,'presets':zone.presets,'time':time.monotonic()})
    await follow.task


async def test_each_confirmed_turn_selects_one_camera(runtime):
    follow=setup(runtime)
    for seat, camera in [('a','one'), ('c','two'), ('presenter','three')]:
        await lock(follow,seat)
        assert runtime.settings.display.layout=='single'
        assert runtime.settings.display.cameras==[camera]
    assert follow.recall.await_count==3


async def test_latest_turn_supersedes_a_cut_during_minimum_hold(runtime):
    follow=setup(runtime)
    await lock(follow,'a')
    entered, release = asyncio.Event(), asyncio.Event()
    async def delay(seconds):
        assert 0 < seconds <= runtime.settings.tracking.min_hold_ms/1000
        entered.set()
        await release.wait()
    with patch('confdeck.follow.asyncio.sleep', side_effect=delay):
        follow.dispatch({'zone':'c','presets':{'two':'21'},'time':time.monotonic()})
        await asyncio.wait_for(entered.wait(), 1)
        assert runtime.settings.display.cameras==['one']
        follow.dispatch({'zone':'presenter','presets':{'three':'21'},'time':time.monotonic()})
        release.set()
        await follow.task
    assert follow.recall.await_count==1
    follow.update_conversation_display(time.monotonic())
    await follow.task
    assert runtime.settings.display.cameras==['three']


async def test_shared_camera_uses_wide_before_moving(runtime):
    follow=setup(runtime)
    runtime.settings.tracking.wide_presets={'three':'25'}
    await lock(follow,'a')
    during=[]
    async def recall(cid, token):
        during.append((cid, list(runtime.settings.display.cameras)))
        return {'ok':True}
    follow.recall=recall
    await lock(follow,'b')
    assert during == [('three',['one']), ('one',['three'])]
    assert runtime.settings.display.cameras==['one']


async def test_ambiguous_speech_then_confirmed_return(runtime):
    follow=setup(runtime)
    settings=runtime.settings.tracking
    settings.wide_presets={'three':'25'}
    settings.conversation_hold_ms=1000
    settings.min_hold_ms=500
    settings.confirm_ms=300
    settings.ema_alpha=1
    settings.level_gate=True
    now=time.monotonic()
    for offset, azimuth in [(0,0),(.4,0),(.6,305),(1,305),(1.7,305),(2,0),(2.5,0)]:
        with patch('confdeck.follow.time.monotonic', return_value=now+offset):
            follow.feed({'azimuth':azimuth,'elevation':30,'level':-20})
        if follow.task:
            await follow.task
        if offset==1:
            assert follow.conversation_current=='a'
        if offset==1.7:
            assert follow.conversation_current=='wide'
    assert follow.conversation_current=='a'


async def test_conflict_shows_newest_and_manual_camera_is_excluded(runtime):
    follow=setup(runtime)
    await lock(follow,'a')
    await lock(follow,'b')
    assert runtime.settings.display.cameras==['one']
    assert follow.camera_frames['one']==('b','22')
    runtime.manual.leases['one']={}
    follow.update_conversation_display(time.monotonic())
    assert runtime.settings.display.cameras==[]
    runtime.manual.leases.clear()


async def test_failed_camera_does_not_hide_successful_view(runtime):
    follow=setup(runtime)
    await lock(follow,'a')
    follow.recall=AsyncMock(side_effect=ValueError('offline'))
    await lock(follow,'c')
    assert runtime.settings.display.cameras==['one']
    assert runtime.settings.display.layout=='single'


async def test_latest_decision_waits_for_pending_command(runtime):
    follow=setup(runtime)
    gate=asyncio.Event()
    async def recall(cid, token):
        await gate.wait()
        return {'ok':True}
    follow.recall=recall
    follow.dispatch({'zone':'a','presets':{'one':'21'},'time':time.monotonic()})
    await asyncio.sleep(0)
    follow.dispatch({'zone':'c','presets':{'two':'21'},'time':time.monotonic()})
    follow.dispatch({'zone':'presenter','presets':{'three':'21'},'time':time.monotonic()})
    assert follow.pending_decision['zone']=='presenter'
    gate.set()
    await follow.task
    follow.update_conversation_display(time.monotonic())
    await follow.task
    assert follow.conversation_current=='presenter'
    assert follow.pending_decision is None


async def test_empty_program_does_not_fall_back_to_all_cameras(runtime):
    setup(runtime)
    runtime.settings.display.cameras=[]
    assert runtime.display_state()['cameras']==[]


async def test_silence_wide_replaces_split(runtime):
    follow=setup(runtime)
    await lock(follow,'a')
    await lock(follow,'c')
    follow.dispatch({'zone':'wide','presets':{'three':'25'},'time':time.monotonic()})
    await follow.task
    follow.update_conversation_display(time.monotonic()+10)
    assert runtime.settings.display.layout=='single'
    assert runtime.settings.display.cameras==['three']
