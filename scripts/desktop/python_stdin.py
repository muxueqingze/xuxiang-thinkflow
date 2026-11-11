"""Run stdin in the bundled interpreter while retaining its isolated stdlib paths."""
import sys
import types


if __name__ == "__main__":
    # The embeddable ._pth deliberately omits cwd; tf-python runs workspace code.
    sys.path.insert(0, "")  # Like python -, imports follow cwd even after chdir.
    sys.argv = ["-"]
    source = sys.stdin.buffer.read()
    main = types.ModuleType("__main__")
    main.__file__ = "<stdin>"
    main.__cached__ = None
    sys.modules["__main__"] = main
    exec(compile(source, "<stdin>", "exec"), main.__dict__)
