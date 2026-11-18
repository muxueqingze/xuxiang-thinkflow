"""Collect notices from the wheels used to freeze the Windows desktop backend."""

from importlib.metadata import distribution, packages_distributions
from pathlib import Path
import sys

from PyInstaller.archive.readers import CArchiveReader


def backend_distributions(backend):
    archive = CArchiveReader(str(backend / "thinkflow-service.exe"))
    pyz, = (name for name in archive.toc if name.endswith(".pyz"))
    modules = {name.partition(".")[0] for name in archive.open_embedded_archive(pyz).toc}
    internal = backend / "_internal"
    for binary in internal.rglob("*.pyd"):
        modules.add(binary.relative_to(internal).parts[0].partition(".")[0])
    owners = packages_distributions()
    names = {package for module in modules for package in owners.get(module, ())}
    names.add("PyInstaller")  # The bootloader is in the executable, outside the PYZ.
    return sorted(names, key=str.casefold)


def wheel_notices(package):
    declared = set(package.metadata.get_all("License-File") or ())
    notices = []
    for file in package.files:
        if not file.parts[0].endswith(".dist-info"):
            continue
        relative = Path(*file.parts[1:])
        if (relative.parts[0] == "licenses" or relative.as_posix() in declared
                or relative.name.upper().startswith(("LICENSE", "LICENCE", "COPYING", "NOTICE", "AUTHORS", "COPYRIGHT"))):
            notices.append(file)
    if not notices:
        raise ValueError(f"No license text found in {package.metadata['Name']} {package.version}")
    return sorted(notices)


def write_notices(app):
    backend = app / "resources" / "backend"
    with (app / "THIRD-PARTY-NOTICES.txt").open("wb") as output:
        output.write(
            b"ThinkFlow Desktop third-party notices\n\n"
            b"Electron: LICENSE and LICENSES.chromium.html\n"
            b"Embedded CPython: resources/backend/python/LICENSE.txt\n"
            b"xterm.js: resources/app.asar, node_modules/@xterm/{xterm,addon-fit}/LICENSE\n"
        )
        for name in backend_distributions(backend):
            package = distribution(name)
            project = package.metadata["Name"]
            source = f"https://pypi.org/project/{project}/{package.version}/#files"
            output.write(f"\n===== {project} {package.version} =====\n{source}\n".encode())
            for file in wheel_notices(package):
                text = package.locate_file(file).read_bytes()
                output.write(f"\n--- {file.name} ---\n".encode() + text + b"\n")
                if name.casefold() == "pathspec" and file.name == "LICENSE":
                    header = f"PathSpec {package.version}\nSource distribution: {source}\n\n"
                    (app / "PathSpec-LICENSE.txt").write_bytes(header.encode() + text)
        native_notices = Path(__file__).with_name("licenses") / "terminal-components.txt"
        output.write(b"\n" + native_notices.read_bytes())


if __name__ == "__main__":
    write_notices(Path(sys.argv[1]))
