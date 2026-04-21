"""`dwc <subcommand>` dispatcher — routes to each module's main()."""
import sys

from . import validate, bootstrap, batch, mhl_walker, watch, sign_example

COMMANDS = {
    "validate":     (validate,     "Validate a sidecar through 9 stages"),
    "bootstrap":    (bootstrap,    "Produce a signed sidecar by re-reading clip bytes"),
    "batch":        (batch,        "Batch-produce sidecars (audit mode, re-hashes each clip)"),
    "mhl-walk":     (mhl_walker,   "Walk a production tree, lift hashes from the MHL"),
    "watch":        (watch,        "Long-running watch-folder service"),
    "sign-example": (sign_example, "Regenerate demo keys and re-sign the example sidecar"),
}


def _print_help() -> int:
    print("usage: dwc <subcommand> [args...]\n")
    print("Available subcommands:")
    width = max(len(name) for name in COMMANDS) + 2
    for name, (_, desc) in COMMANDS.items():
        print(f"  {name:<{width}}{desc}")
    print("\nPass --help after a subcommand for its flags, e.g.  dwc validate --help")
    return 0


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        return _print_help()
    cmd = sys.argv[1]
    if cmd not in COMMANDS:
        print(f"dwc: unknown subcommand '{cmd}'", file=sys.stderr)
        print("     run 'dwc --help' to see available subcommands", file=sys.stderr)
        return 2
    module, _ = COMMANDS[cmd]
    # Reshape argv so the module's own argparse sees its flags as if invoked directly.
    sys.argv = [f"dwc {cmd}"] + sys.argv[2:]
    result = module.main(sys.argv) if cmd == "validate" else module.main()
    return result if isinstance(result, int) else 0


if __name__ == "__main__":
    sys.exit(main())
