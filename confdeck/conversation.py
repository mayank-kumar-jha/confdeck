"""Display composition from confirmed turns, not an overlap detector."""
from itertools import product


def compose(zones, seats, available):
    mappings = [next((z.presets for z in zones if z.id == seat), {}) for seat in seats]
    options = [[(cid, token) for cid, token in mapping.items() if cid in available] for mapping in mappings]
    if not options or any(not row for row in options):
        return {}
    for selection in product(*options):
        if len({cid for cid, _ in selection}) == len(selection):
            return dict(selection)
    return {}


def plan(zones, seats, available, frames=None, presenter_seat="", presenter_camera=""):
    """Bounded best coverage: newest seat first, then recent seats, then fewer moves."""
    frames = frames or {}
    seats = list(dict.fromkeys(seats))[:6]
    mappings = {z.id: z.presets for z in zones}
    best, score = {}, None

    def visit(index, assigned):
        nonlocal best, score
        if index == len(seats) or len(assigned) == 3:
            included = tuple(int(s in assigned) for s in seats)
            rank = (included[0] if included else 0, len(assigned), included,
                    sum(frames.get(cid) == (s, token) for s, (cid, token) in assigned.items()))
            if score is None or rank > score:
                score, best = rank, dict(assigned)
            return
        seat = seats[index]
        used = {cid for cid, _ in assigned.values()}
        for cid, token in mappings.get(seat, {}).items():
            if cid not in available or cid in used:
                continue
            if presenter_camera and presenter_seat:
                if (seat == presenter_seat) != (cid == presenter_camera):
                    continue
            assigned[seat] = (cid, token)
            visit(index+1, assigned)
            del assigned[seat]
        visit(index+1, assigned)

    visit(0, {})
    return best
