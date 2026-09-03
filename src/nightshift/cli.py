"""nightshift - one entry point for both views.

    nightshift            the pixel office in a browser
    nightshift board      the terminal table
    nightshift talk       read any session's conversation, and reply to it

Anything after the subcommand is passed straight through.
"""
import sys

USAGE = """nightshift - who on this Mac is working, and who is waiting on you

  nightshift                 pixel office, opens a browser
  nightshift --port 9000     serve somewhere else
  nightshift --no-open       serve without opening a browser

  nightshift board           terminal table, refreshes every 2s
  nightshift board --once    print once and exit
  nightshift board -n 5      refresh every 5s
  nightshift board --brief   one line, for a tmux status bar

  nightshift herdr           what the herdr backend sees (diagnostic)

  nightshift talk            conversation reader + compose box, opens a browser
  nightshift talk --port N   serve somewhere else
  nightshift talk --no-open  serve without opening a browser
"""


def main():
    args = sys.argv[1:]
    if args and args[0] in ("-h", "--help", "help"):
        print(USAGE)
        return
    if args and args[0] == "herdr":
        from .herdr import main as diag
        return diag()
    if args and args[0] in ("talk", "read"):          # `read` is the old name
        from .talk import main as talk
        sys.argv = ["nightshift talk"] + args[1:]
        return talk()
    if args and args[0] == "board":
        from .fleet import main as board
        sys.argv = ["nightshift board"] + args[1:]
        return board()
    from .office import main as office
    sys.argv = ["nightshift"] + args
    return office()


if __name__ == "__main__":
    main()
