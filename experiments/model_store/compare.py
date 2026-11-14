"""Offline model-store experiment. Results and generated packs belong outside Git."""
import argparse
import gc
import hashlib
import importlib.util
import json
import os
import platform
import random
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from experiments.model_store.store import NativeModel, pack_model, unpack_model  # noqa: E402
from tests.fakes import keys_for_text, make_keymap  # noqa: E402
from wayswitch.detector import Detector, LanguageModel  # noqa: E402


def memory():
    gc.collect()
    path = Path('/proc/self/smaps_rollup')
    if not path.exists():
        return {'unavailable': 'Linux smaps_rollup required'}
    return {line.split(':')[0]: int(line.split()[1]) for line in path.read_text().splitlines()
            if line.startswith(('Rss:', 'Pss:', 'Private_Clean:', 'Private_Dirty:'))}


def summary(values):
    values = sorted(values)
    def q(f):
        at = (len(values)-1)*f
        i = int(at)
        return values[i] + (values[min(i+1, len(values)-1)] - values[i])*(at-i)
    return {'n': len(values), 'p50': q(.5), 'p95': q(.95),
            'p99': q(.99) if len(values) >= 100 else None, 'max': max(values)}


def load(variant, packs):
    if variant == 'original':
        return {lang: LanguageModel.load(lang) for lang in ('ru', 'en')}
    if variant == 'native':
        return {lang: NativeModel.load(lang, packs) for lang in ('ru', 'en')}
    return {lang: unpack_model((packs / (lang+'.bin')).read_bytes()) for lang in ('ru', 'en')}


def ready(models):
    for model in models.values():
        if not isinstance(model, NativeModel):
            _ = model.endings, model._sorted_words


def workload():
    reference = {lang: LanguageModel.load(lang) for lang in ('ru', 'en')}
    km, rng, work = make_keymap(), random.Random(74), []
    for lang, group in (('en', 0), ('ru', 1)):
        words = sorted(w for w in reference[lang].words if 4 <= len(w) <= 24)
        rng.shuffle(words)
        for word in words[:1500]:
            try:
                keys = keys_for_text(km, word, group)
            except KeyError:
                continue
            work.extend([(keys, group, 1-group), (keys, 1-group, group)])
        alphabet = ('abcdefghijklmnopqrstuvwxyz' if lang == 'en'
                    else 'абвгдеёжзийклмнопрстуфхцчшщъыэюя')
        for _ in range(500):
            word = ''.join(rng.choice(alphabet) for _ in range(rng.randint(4, 24)))
            work.append((keys_for_text(km, word, group), group, 1-group))
    rng.shuffle(work)
    return km, work


def decision(dec, group, other):
    return [dec.action, dec.target_text, dec.reason, group if dec.action == 'keep' else other,
            dec.confidence, dec.a.score, dec.b.score]


def child(args):
    phases = {'imported': memory()} if args.child == 'memory' else {}
    started = time.perf_counter_ns()
    models = load(args.variant, args.packs)
    loaded = time.perf_counter_ns()
    ready(models)
    indexed = time.perf_counter_ns()
    result = {'load_ns': loaded-started, 'ready_ns': indexed-started}
    if args.child == 'memory':
        phases['ready'] = memory()
        det = Detector(make_keymap(), models)
        words = [('en', 'synthetic'+''.join(chr(97+(i//26**j)%26) for j in range(4)))
                 for i in range(5000)]
        begin = time.perf_counter_ns()
        det.set_personal_words(words)
        ready(det.models)
        result['overlay_ready_ns'] = time.perf_counter_ns()-begin
        phases['overlay_5000'] = memory()
        result['memory_kib'] = phases
    elif args.child == 'decisions':
        km, work = workload()
        det = Detector(km, models)
        expected = [decision(det.decide(keys, g, og), g, og) for keys, g, og in work]
        durations, cpu = [], []
        for _ in range(5):
            start_cpu = time.process_time_ns()
            for keys, g, og in work:
                begin = time.perf_counter_ns()
                det.decide(keys, g, og)
                durations.append(time.perf_counter_ns()-begin)
            cpu.append(time.process_time_ns()-start_cpu)
        result.update({'decisions': expected, 'decide_ns': summary(durations),
                       'batch_cpu_ns': summary(cpu), 'work_items': len(work)})
    print(json.dumps(result, ensure_ascii=False))


def golden(root, packs):
    source = root / 'tools/benchmark_focus.py'
    spec = importlib.util.spec_from_file_location('store_golden', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    py = module.run_golden()
    module._MODELS = load('native', packs)
    native = module.run_golden()
    differences = [{'id': a['id'], 'python': a, 'native': b}
                   for a, b in zip(py['cases'], native['cases'], strict=True) if a != b]
    assert len(py['cases']) == len(native['cases'])
    return {'corpus_sha256': py['corpus']['sha256'], 'cases': len(py['cases']),
            'python_statuses': {s: sum(c['status'] == s for c in py['cases'])
                                for s in ('pass', 'fail', 'error')},
            'native_statuses': {s: sum(c['status'] == s for c in native['cases'])
                                for s in ('pass', 'fail', 'error')},
            'differences': differences}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--packs', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--golden-root', type=Path)
    parser.add_argument('--export', action='store_true')
    parser.add_argument('--child', choices=('load', 'memory', 'decisions'))
    parser.add_argument('--variant', choices=('original', 'packed_python', 'native'))
    args = parser.parse_args()
    if args.child:
        return child(args)
    args.packs.mkdir(parents=True, exist_ok=True)
    if args.export:
        start = time.perf_counter_ns()
        for lang in ('ru', 'en'):
            (args.packs / (lang+'.bin')).write_bytes(pack_model(LanguageModel.load(lang)))
        print(json.dumps({'export_ns': time.perf_counter_ns()-start}))
        return
    if args.output is None:
        parser.error('--output required')
    report = {'schema_version': 1, 'seed': 74,
              'environment': {'python': platform.python_version(), 'platform': platform.platform()},
              'packs': {f.name: {'bytes': f.stat().st_size,
                                 'sha256': hashlib.sha256(f.read_bytes()).hexdigest()}
                        for f in sorted(args.packs.glob('*.bin'))},
              'original_data': {f.name: {'bytes': f.stat().st_size,
                                         'sha256': hashlib.sha256(f.read_bytes()).hexdigest()}
                                for f in sorted((ROOT/'wayswitch/data').glob('*.gz'))},
              'variants': {}}
    binary = os.environ.get('WAYSWITCH_MODEL_STORE_LIB')
    report['native_library'] = ({'sha256': hashlib.sha256(Path(binary).read_bytes()).hexdigest(),
                                 'bytes': Path(binary).stat().st_size} if binary else None)
    report['source_hashes'] = {
        name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
        for name in ('wayswitch/detector.py', 'wayswitch/ngram.py', 'wayswitch/keymap.py',
                     'experiments/model_store/store.rs', 'experiments/model_store/store.py',
                     'experiments/model_store/compare.py', 'experiments/model_store/Cargo.toml')}
    raw_decisions = {}
    for variant in ('original', 'packed_python', 'native'):
        def run(kind, variant=variant):
            out = subprocess.check_output([sys.executable, str(Path(__file__).resolve()),
                                           '--packs', str(args.packs), '--variant', variant,
                                           '--child', kind], text=True)
            return json.loads(out)
        samples = [run('load') for _ in range(20)]
        measured = run('memory')
        measured.update({'load_ns': summary([s['load_ns'] for s in samples]),
                         'ready_ns': summary([s['ready_ns'] for s in samples]),
                         'load_samples': samples})
        performance = run('decisions')
        raw_decisions[variant] = performance.pop('decisions')
        measured.update(performance)
        # The one decision process's startup is separate from the 20-process summary.
        measured['load_ns'] = summary([s['load_ns'] for s in samples])
        measured['ready_ns'] = summary([s['ready_ns'] for s in samples])
        report['variants'][variant] = measured
    report['differences'] = {}
    ref = raw_decisions['original']
    for variant, decisions in raw_decisions.items():
        assert len(decisions) == len(ref)
        pairs = zip(ref, decisions, strict=True)
        report['differences'][variant] = [{'index': i, 'python': a, 'candidate': b}
                                         for i, (a, b) in enumerate(pairs) if a != b]
    if args.golden_root:
        report['golden'] = golden(args.golden_root, args.packs)
    report['limits'] = [
        'Offline Python process with two current models; not complete daemon/GUI RSS',
        'One process for FFI; no helper. Fresh-process load uses warm OS file cache',
        'Packed format is experimental, generated from original gzip/JSON; export is separate',
        'Native eagerly builds indices; compare ready_ns with both Python indices materialized',
        'Expanded workload models are known current words, not held-out quality evaluation',
        'No Rust Detector, device code, IPC process, new languages or production integration',
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'differences': {k: len(v) for k, v in report['differences'].items()},
                      'golden': report.get('golden')}, ensure_ascii=False))
    golden_report = report.get('golden', {})
    failed = any(report['differences'].values()) or bool(golden_report.get('differences'))
    for variant in ('python_statuses', 'native_statuses'):
        status = golden_report.get(variant, {})
        failed |= bool(status.get('fail') or status.get('error'))
    return int(failed)


if __name__ == '__main__':
    raise SystemExit(main())
