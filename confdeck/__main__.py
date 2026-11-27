import sys
from confdeck.cli import main

if __name__ == "__main__":
    if len(sys.argv) == 1:
        sys.argv.append("start")
    main()

