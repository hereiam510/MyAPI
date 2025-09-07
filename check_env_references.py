import os
import re

def get_env_keys(env_path):
    keys = set()
    with open(env_path, "r") as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#") and line:
                k = line.split("=")[0].strip()
                if k:
                    keys.add(k)
    return keys

def py_files_in_dir(directory):
    py_files = []
    for root, dirs, files in os.walk(directory):
        for fname in files:
            if fname.endswith(".py"):
                py_files.append(os.path.join(root, fname))
    return py_files

def find_references(keys, py_files):
    used = set()
    key_patterns = [re.compile(re.escape(k)) for k in keys]  # direct string matching
    for py_file in py_files:
        with open(py_file, "r", encoding="utf-8") as f:
            try:
                code = f.read()
            except:
                continue  # skip binary/malformed files
        for k in keys:
            # os.getenv('KEY') or os.getenv("KEY")
            if re.search(r"os\.getenv\(['\"]{}['\"]".format(re.escape(k)), code):
                used.add(k)
            # direct string use (e.g., in f"..." or string assignments)
            elif any(pat.search(code) for pat in key_patterns if pat.pattern == re.escape(k)):
                used.add(k)
    return used

if __name__ == "__main__":
    env_file = ".env"
    root_dir = "."

    env_keys = get_env_keys(env_file)
    py_files = py_files_in_dir(root_dir)
    used_keys = find_references(env_keys, py_files)

    missing = env_keys - used_keys

    print(f"Scanned {len(py_files)} Python file(s).")
    if missing:
        print("❌ The following .env keys are NOT referenced in any Python file:")
        for k in sorted(missing):
            print("   ", k)
    else:
        print("✅ All .env keys are referenced in your Python code.")

    # For extra verbosity, print keys found and which files:
    # for k in sorted(used_keys):
    #     print("[USED]", k)


# How to Use (Requires Python 3)
# Run it with 
# python check_env_references.py

