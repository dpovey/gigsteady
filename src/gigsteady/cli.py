"""gigsteady command line."""
import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gigsteady", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("probe", help="show what gigsteady sees in a clip")
    p.add_argument("clip")
    a = ap.parse_args(argv)
    if a.cmd == "probe":
        from .probe import probe
        print(probe(a.clip))
    return 0


if __name__ == "__main__":
    sys.exit(main())
