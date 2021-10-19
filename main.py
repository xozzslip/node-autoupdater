# Components and steps:
# 1. watch for updates in github
# 2. picker: decide what commit should be used
# 3. compare current version and what picker picked
# 4. patcher: patch source code
# 5. builder: build source code
# 6. runner: start service


# what arguments do I need?
# - name of the node: eth/bsc/...
# - working dir: where the source code (working dir/source) and binaries (working dir/binaries)
# are saved (~/<node name> by default)
# - supervisor ctl config name and path (defined by <node name> by default)
# github repository
import subprocess
import re
import os


def subprocess_run(cmd: str, path: str) -> str:
    cwd = os.getcwd()
    try:
        os.chdir(path)
        return subprocess.run(cmd, check=True, shell=True, capture_output=True, encoding="utf-8").stdout.rstrip()
    except subprocess.CalledProcessError as e:
        print(f"command {cmd} failed with stderr: {e.stderr}")
        raise e
    finally:
        os.chdir(cwd)


def latest_version(source_dir: str) -> str:
    subprocess_run("git fetch", source_dir)
    return subprocess_run("git tag --sort=v:refname | tail -n1", source_dir)


def current_version(supervisor_config_file: str):
    with open(supervisor_config_file) as f:
        supervisor_config = f.read()
    m = re.search("command=[\w\/]+_(v[0-9\.]+)", supervisor_config)
    groups = m.groups()
    if len(groups) == 0:
        raise ValueError(f"failed to parse supervisor config file {supervisor_config_file}")
    return groups[0]


def checkout(source_dir: str, version: str):
    subprocess_run(f"git checkout .", path=source_dir)
    subprocess_run(f"git checkout {version}", path=source_dir)


def find_patch_file(source_dir: str):
    file = subprocess_run(f"grep -r --include='*.go' --files-with-matches 'PublicBlockChainAPI)' {source_dir}", ".")

    print(file)
    if not os.path.isfile(file):
        raise ValueError(f"failed to find file to patch")
    return file


def patch(file_to_patch: str):
    print(os.getcwd())
    with open(file_to_patch) as f:
        code = f.read()
    with open("./patch.go") as f:
        patch_code = f.read()

    code += "\n"
    code += patch_code
    with open(file_to_patch, "w") as f:
        f.write(code)


def build(source_dir: str):
    subprocess_run("make geth", path=source_dir)


def move_binary(version: str, binary_path: str, binary_dir: str):
    if not os.path.isdir(binary_dir):
        os.mkdir(binary_dir)
    _, filename = os.path.split(binary_path)
    subprocess_run(f"cp {binary_path} {binary_dir}/{filename}_{version}", ".")


def tests():
    print(current_version("./supervisor_example.conf"))
    assert current_version("./supervisor_example.conf") == "v1.10.6"

    source_dir = "/home/khasan/trash/patched/go-ethereum"
    version = latest_version(source_dir)
    print(version)
    checkout(source_dir, version)
    patch(find_patch_file(source_dir))
    build(source_dir)
    move_binary(version, os.path.join(source_dir, "eth_node"), "./binary")

# Press the green button in the gutter to run the script.
if __name__ == '__main__':
    tests()

# See PyCharm help at https://www.jetbrains.com/help/pycharm/
