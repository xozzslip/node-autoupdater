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
import sys
import random
import time
import argparse

PATCH = """
func (s *PublicBlockChainAPI) GetFullBlockByNumber(ctx context.Context, number rpc.BlockNumber, fullTx bool) (map[string]interface{}, error) {
	block, err := s.b.BlockByNumber(ctx, number)
	if err != nil || block == nil {
		return nil, err
	}
	response, err := s.rpcMarshalBlock(ctx, block, true, fullTx)
	if err != nil {
		return nil, err
	}
	// Pending blocks need to nil out a few fields
	if number == rpc.PendingBlockNumber {
		for _, field := range []string{"hash", "nonce", "miner"} {
			response[field] = nil
		}
	}
	if !fullTx {
		return response, err
	}
	txs := response["transactions"].([]interface{})
	// build map with sender for every tx hash
	txByHash := make(map[common.Hash]*RPCTransaction)
	for _, rawTx := range txs {
		tx, ok := rawTx.(*RPCTransaction)
		if !ok {
			return nil, fmt.Errorf("can't get RPC tx: %s", tx.Hash.Hex())
		}
		txByHash[tx.Hash] = tx
	}
	response["receipts"], err = s.getBlockReceipts(ctx, txByHash, block)
	return response, err
}

func (s *PublicBlockChainAPI) getBlockReceipts(ctx context.Context, txByHash map[common.Hash]*RPCTransaction, block *types.Block) ([]map[string]interface{}, error) {
	// get all block receipts from database
	receipts, err := s.b.GetReceipts(ctx, block.Hash())
	if err != nil {
		return nil, err
	}
	// build result array with packed receipts
	result := make([]map[string]interface{}, 0)
	for _, receipt := range receipts {
		tx, ok := txByHash[receipt.TxHash]
		if !ok {
			log.Error("Can't find transaction sender in cache", "hash", receipt.TxHash)
			continue
		}
		fields := map[string]interface{}{
			"blockHash":         block.Hash(),
			"blockNumber":       "0x" + block.Number().Text(16),
			"transactionHash":   tx.Hash,
			"transactionIndex":  tx.TransactionIndex,
			"from":              tx.From,
			"to":                tx.To,
			"gasUsed":           hexutil.Uint64(receipt.GasUsed),
			"cumulativeGasUsed": hexutil.Uint64(receipt.CumulativeGasUsed),
			"contractAddress":   nil,
			"logs":              receipt.Logs,
			"logsBloom":         receipt.Bloom,
			"type":              tx.Type,
		}
		if len(receipt.PostState) > 0 {
			fields["root"] = hexutil.Bytes(receipt.PostState)
		} else {
			fields["status"] = hexutil.Uint(receipt.Status)
		}
		if receipt.Logs == nil {
			fields["logs"] = [][]*types.Log{}
		}
		if receipt.ContractAddress != (common.Address{}) {
			fields["contractAddress"] = receipt.ContractAddress
		}
		result = append(result, fields)
	}
	return result, nil
}
"""


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
    m = re.search("command=[\.\w\/]+_(v[0-9\.]+)", supervisor_config)
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
    code += "\n"
    code += PATCH
    with open(file_to_patch, "w") as f:
        f.write(code)


def build(source_dir: str):
    subprocess_run("make geth", path=source_dir)


def move_binary(version: str, binary_path: str, binary_dir: str) -> str:
    if not os.path.isdir(binary_dir):
        os.mkdir(binary_dir)
    _, filename = os.path.split(binary_path)
    new_binary_path = f"{binary_dir}/{filename}{random.randint(0, 1000)}_{version}"
    subprocess_run(f"cp {binary_path} {new_binary_path}", ".")
    return new_binary_path


def rewrite_supervisor_config(binary_path: str, supervisor_config_file: str):
    with open(supervisor_config_file) as f:
        supervisor_config = f.read()
    m = re.search("command=[\.\w\/]+_(v[0-9\.]+)", supervisor_config)
    command = m.group()
    supervisor_config = supervisor_config.replace(command, f"command={binary_path}")
    with open(supervisor_config_file, "w") as f:
        f.write(supervisor_config)


def update_supervisor():
    subprocess_run("sudo supervisorctl reread", ".")
    subprocess_run("sudo supervisorctl update", ".")


def execute(supervisor_config_file: str, source_dir: str, binary_dir: str, only_new: bool):
    print(f"executing autoupdate for supervisor={supervisor_config_file} sources={source_dir} binaries={binary_dir}")
    latest_v = latest_version(source_dir)
    current_v = current_version(supervisor_config_file)
    print(f"latest version available on git is {latest_v}, current {current_v}")
    if latest_v == current_v and only_new:
        print(f"current and latest version are equal, no update required, exiting...")
        return
    checkout(source_dir, latest_v)
    print(f"checkout of {source_dir} to {latest_v} was executed")
    to_patch = find_patch_file(source_dir)
    print(f"file {to_patch} is going to be patched")
    patch(to_patch)
    print(f"building...")
    build(source_dir)
    print(f"building finished")
    binary_path = move_binary(latest_v, os.path.join(source_dir, "build", "bin", "geth"), binary_dir)
    print(f"rewriting supervisor config with new file")
    rewrite_supervisor_config(binary_path, supervisor_config_file)
    print(f"updating supervisor")
    update_supervisor()
    print(f"updated, checking status...")
    time.sleep(3)
    subprocess.run("sudo supervisorctl status", check=False, shell=True, capture_output=False, encoding="utf-8")


def tests():
    print(current_version("./supervisor_example.conf"))
    assert current_version("./supervisor_example.conf") == "v1.10.6"

    source_dir = "/home/khasan/trash/patched/go-ethereum"
    binary_dir = "/home/khasan/code/gitwatcher/binary"
    version = latest_version(source_dir)
    print(version)
    checkout(source_dir, version)
    patch(find_patch_file(source_dir))
    build(source_dir)
    binary_path = move_binary(version, os.path.join(source_dir, "eth_node"), binary_dir)
    rewrite_supervisor_config(binary_path, "./supervisor_example2.conf")


def main():
    parser = argparse.ArgumentParser(description='Run update of EVM node')
    parser.add_argument('--config', help='path to supervisorctl config file')
    parser.add_argument('--src', help='path to sources of EVM node with .git inside')
    parser.add_argument('--bin', help='path to directory for binaries')
    parser.add_argument('--new', help='skip update if there is no new version in git', action='store_true')
    args = parser.parse_args()
    config = args.config
    sources_path = args.src
    binary_path = args.bin
    only_new = args.new
    if not os.path.isdir(sources_path):
        raise ValueError(f"sources_path {sources_path} should be a dir")
    if not os.path.isdir(binary_path):
        raise ValueError(f"binary_path {binary_path} should be a dir")
    if not os.path.isfile(config):
        raise ValueError(f"config {config} should be a file")
    find_patch_file(sources_path) # check that sources path is good
    binary_path = os.path.abspath(binary_path)
    sources_path = os.path.abspath(sources_path)
    execute(config, sources_path, binary_path, only_new)


if __name__ == '__main__':
    main()
