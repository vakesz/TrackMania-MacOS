#!/usr/bin/env python3
"""Build test-only wined3d.dll from pristine pinned vkd3d sources in new scratch.

Rebuild every vkd3d object; reuse only unrelated recorded Wine link inputs.
Never run make or modify the prepared source/work/runtime. --baseline uses the
prepared vkd3d diff, for red/green comparison. DLLs are native-marked app-local
diagnostic overlays, not installable runtime replacements.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[4]
PIN = "36b6a2cf679fb395f668a917b76537190e212d9c"


def build(output, baseline=False, arch="i386"):
    source = ROOT / ".build/release/sources/wine"
    work = ROOT / ".build/release/work/wine"
    output = output.parent.resolve(strict=True) / output.name
    if output.exists() or output.is_symlink() or ROOT == output or ROOT in output.parents:
        raise ValueError("output must be a new directory outside the checkout")
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(key, None)
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=source, env=env)
    if git("rev-parse", "HEAD").decode().strip() != PIN:
        raise ValueError("unexpected Wine source pin")
    archive = git("archive", PIN, "libs/vkd3d")
    patches = [git("diff", "HEAD", "--", "libs/vkd3d")] if baseline else [
        (ROOT / "patches/wine" / name).read_bytes() for name in (
            "0001-vkd3d-shader-lower-texm3x3.patch", "0006-vkd3d-hlsl-legacy-function-targets.patch")]
    output.mkdir(mode=0o700)
    (output / "source").mkdir()
    (output / "tmp").mkdir()
    env.update(TMPDIR=str(output / "tmp"), TMP=str(output / "tmp"), TEMP=str(output / "tmp"))
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(output / "source", filter="data")
    for patch in patches:
        subprocess.run(["git", "apply", "--check", "-"], input=patch, cwd=output / "source", env=env, check=True)
        subprocess.run(["git", "apply", "-"], input=patch, cwd=output / "source", env=env, check=True)
    text = (work / "Makefile").read_text().replace("\\\n", "")
    generated = "libs/vkd3d/libs/vkd3d-shader/hlsl.tab.c"
    library = f"libs/vkd3d/{arch}-windows/libvkd3d.a"
    dll = f"dlls/wined3d/{arch}-windows/wined3d.dll"
    def recipe(target):
        match = re.search(r"^" + re.escape(target) + r":[^\n]*\n\t([^\n]+)", text, re.M)
        if not match:
            raise ValueError("missing recipe " + target)
        command = match[1].replace("$@", str(output / target))
        for variable in re.findall(r"\$\((\w+)\)", command):
            if variable not in {arch + "_CC", arch + "_CFLAGS", arch + "_LDFLAGS"}:
                raise ValueError("unexpected variable " + variable)
            command = command.replace("$(" + variable + ")", re.search(r"^" + variable + r" = (.*)$", text, re.M)[1])
        if "$" in command:
            raise ValueError("unsupported recipe expansion")
        return shlex.split(command)
    objects = [arg for arg in recipe(library) if arg.endswith(".o")]
    targets = [generated, *objects, library, dll]
    commands = []
    for target in targets:
        (output / target).parent.mkdir(parents=True, exist_ok=True)
        args = recipe(target)
        for i, arg in enumerate(args):
            if arg == "-Wl,--wine-builtin":
                args[i] = "-Wl,--no-insert-timestamp"
            elif arg == ".":
                args[i] = str(work)
            elif str(source / "libs/vkd3d") in arg:
                args[i] = arg.replace(str(source / "libs/vkd3d"), str(output / "source/libs/vkd3d"))
            elif arg == generated or arg in objects or arg == library:
                args[i] = str(output / arg)
            elif arg.startswith("-I") and not Path(arg[2:]).is_absolute():
                args[i] = "-I" + str(work / arg[2:])
            elif arg.startswith(("dlls/", "libs/", "tools/")):
                args[i] = str(work / arg)
        if args.count("-o") != 1 or args[args.index("-o") + 1] != str(output / target):
            raise ValueError("output escaped scratch")
        commands.append(args)
    (output / "build.json").write_text(json.dumps({"pin": PIN, "baseline": baseline,
        "patch_sha256": [hashlib.sha256(p).hexdigest() for p in patches], "commands": commands}, indent=2))
    with (output / "build.log").open("w") as log:
        for command in commands:
            print(shlex.join(command), file=log, flush=True)
            subprocess.run(command, cwd=output, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
    print(output / dll)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline", action="store_true")
    parser.add_argument("--arch", choices=("i386", "x86_64"), default="i386")
    args = parser.parse_args()
    build(args.output, args.baseline, args.arch)
