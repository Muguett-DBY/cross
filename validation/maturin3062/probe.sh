set -euo pipefail
triple="$1"
scratch="$(mktemp -d)"
trap 'rm -rf -- "$scratch"' EXIT
old="/usr/local/bin/maturin"
"$old" --version
python3.14 --version
python3.14t -c 'import struct,sysconfig; print("free-threaded",struct.calcsize("P")*8,sysconfig.get_config_var("Py_GIL_DISABLED")); assert sysconfig.get_config_var("Py_GIL_DISABLED") == 1'
curl --proto '=https' --tlsv1.2 -LsSf https://github.com/PyO3/maturin/releases/download/v1.15.0/maturin-x86_64-unknown-linux-musl.tar.gz -o "$scratch/maturin.tar.gz"
sha256sum "$scratch/maturin.tar.gz"
tar -xz -C "$scratch" -f "$scratch/maturin.tar.gz"
"$scratch/maturin" --version
"$old" build --release --target "$triple" --find-interpreter --out old-find
"$scratch/maturin" build --release --target "$triple" --find-interpreter --out new-find
python3.14 verify.py "$triple"
