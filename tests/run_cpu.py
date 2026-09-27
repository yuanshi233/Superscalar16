"""Compare native Logisim CPU execution with the independent ISA oracle.

Usage: python tests/run_cpu.py --names basic_isa_lane_mix branch_wrong_path_store_halt
Requires the Logisim-evolution 5.0.0 jar and a Java 21 JDK.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from isa import ReferenceCPU  # noqa: E402

DEFAULT_JAR = Path(r"D:\logisim\app\logisim-evolution-5.0.0-all.jar")
DEFAULT_JDK = Path(r"C:\Users\yuanshi\.codex\cache\jdk21\extract\jdk-21.0.12.1+1\bin")
KEY_VALUE = re.compile(r"([\w]+)=([\w]+)")


def tool(name):
    configured = os.environ.get("LOGISIM_JDK_BIN")
    if configured:
        return str(Path(configured) / name)
    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        return str(Path(java_home) / "bin" / name)
    return str(DEFAULT_JDK / name) if (DEFAULT_JDK / name).is_file() else name


def compare_case(case, lines):
    name = case["name"]
    oracle = ReferenceCPU(case["words"])
    expected = case["expected"]
    retirements = 0
    final = None
    errors = []
    controls = {event["cycle"]: event for event in case.get("controls", [])}
    for line in lines:
        if line.startswith("RESET cycle="):
            oracle.reset()
            retirements = 0
        elif line.startswith("RETIRE "):
            fields = {key: int(value) for key, value in KEY_VALUE.findall(line)}
            pc = oracle.state.pc
            instruction = case["words"][pc] if pc < len(case["words"]) else 0
            op = instruction >> 12
            if fields["pc"] != pc or fields["op"] != op:
                errors.append(f"retirement {retirements}: pc/op expected {pc}/{op}, "
                              f"got {fields['pc']}/{fields['op']} at cycle {fields['cycle']}")
                break
            if fields["sn"] != (retirements & 255):
                errors.append(f"retirement {retirements}: sequence expected "
                              f"{retirements & 255}, got {fields['sn']}")
                break
            before = oracle.state.registers.copy()
            oracle.step()
            rd = (instruction >> 9) & 7
            if op in (1, 2, 3, 4, 5, 6, 7, 8, 12, 13, 14) and rd:
                result = oracle.state.registers[rd]
                if fields["result"] != result:
                    errors.append(f"retirement {retirements} pc={pc}: result expected "
                                  f"{result}, got {fields['result']} at cycle {fields['cycle']}")
                    break
            retirements += 1
        elif line.startswith("CONTROL_FAIL") or line.startswith("CAPACITY_FAIL") or line.startswith("OSCILLATION"):
            errors.append(line)
        elif line.startswith("CASE_END "):
            final = {key: int(value) if value.isdigit() else value
                     for key, value in KEY_VALUE.findall(line)}
    if final is None:
        errors.append("missing CASE_END (simulator may have failed)")
        return errors
    if final["status"] != "HALTED":
        errors.append(f"status {final['status']} after {final['cycles']} cycles")
    if final["failures"]:
        errors.append(f"native failures={final['failures']}")
    if retirements != expected["retired"]:
        errors.append(f"retired expected {expected['retired']}, got {retirements}")
    if oracle.state.pc != expected["architectural_next_pc"]:
        errors.append(f"architectural next PC expected {expected['architectural_next_pc']}, "
                      f"got {oracle.state.pc}")
    for reg, value in enumerate(expected["registers"]):
        actual = final[f"r{reg}"]
        if actual != value:
            errors.append(f"r{reg} expected {value}, got {actual}")
    for address, value in expected["memory"].items():
        actual = final[f"mem_{address}"]
        if actual != value:
            errors.append(f"memory[{address}] expected {value}, got {actual}")
    if final["max_fifo_count"] > 8 or final["max_sb_count"] > 8:
        errors.append("FIFO/store-buffer overflow")
    if final["max_rob0_count"] > 16 or final["max_rob1_count"] > 16 or final["max_rob2_count"] > 16:
        errors.append("ROB overflow")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--circuit", type=Path, default=ROOT / "Superscalar16_impl.circ")
    parser.add_argument("--programs", type=Path, default=ROOT / "tests" / "programs.json")
    parser.add_argument("--names", nargs="+", help="run only named cases")
    parser.add_argument("--quick", action="store_true", help="skip cases with max_cycles > 1000")
    args = parser.parse_args()
    jar = Path(os.environ.get("LOGISIM_JAR", DEFAULT_JAR))
    if not jar.is_file():
        parser.error(f"Logisim jar not found: {jar} (set LOGISIM_JAR)")
    cases = json.loads(args.programs.read_text(encoding="utf-8"))["programs"]
    if args.names:
        selected = [case for case in cases if case["name"] in args.names]
        missing = set(args.names) - {case["name"] for case in selected}
        if missing:
            parser.error(f"unknown programs: {', '.join(sorted(missing))}")
        cases = selected
    if args.quick:
        cases = [case for case in cases if case["max_cycles"] <= 1000]
    if not cases:
        parser.error("no programs selected")
    source = ROOT / "tools" / "LogisimHarness.java"
    compiled = ROOT / "tools" / "LogisimHarness.class"
    if not compiled.is_file() or source.stat().st_mtime > compiled.stat().st_mtime:
        subprocess.run([tool("javac.exe"), "-cp", str(jar), str(source)],
                       cwd=ROOT, check=True)
    command = [tool("java.exe"), "-Xmx2g", "-cp", os.pathsep.join((str(jar), str(ROOT))),
               "tools.LogisimHarness", str(args.circuit), "--suite"]
    request = []
    for case in cases:
        controls = ";".join(f"{event['cycle']}:{event.get('run', -1)}:{event.get('rst', -1)}"
                            for event in case.get("controls", []))
        request.append("\t".join(("CASE", case["name"], str(case["max_cycles"]),
                                  ",".join(f"{word:04x}" for word in case["words"]),
                                  controls, ",".join(map(str, case["memory_probes"])))))
    result = subprocess.run(command, input="\n".join(request) + "\n", text=True,
                            encoding="utf-8", stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            cwd=ROOT)
    if result.stderr.strip():
        print(result.stderr.strip(), file=sys.stderr)
    groups = {}
    current = None
    for line in result.stdout.splitlines():
        if line.startswith("CASE_BEGIN "):
            current = line.removeprefix("CASE_BEGIN ")
            groups[current] = []
        elif current is not None:
            groups[current].append(line)
    failed = 0
    for case in cases:
        lines = groups.get(case["name"], [])
        errors = compare_case(case, lines)
        end = next((line for line in lines if line.startswith("CASE_END ")), "")
        if errors:
            failed += 1
            print(f"FAIL {case['name']}: {'; '.join(errors[:5])}")
            if end:
                print(f"  {end}")
        else:
            print(f"PASS {case['name']}: {end.removeprefix('CASE_END ')}")
    print(f"RESULT {len(cases) - failed}/{len(cases)} programs passed")
    if result.returncode not in (0, 1):
        print(f"Harness exited {result.returncode}; last output: {result.stdout[-2000:]}",
              file=sys.stderr)
    return 1 if failed or result.returncode else 0


if __name__ == "__main__":
    raise SystemExit(main())
