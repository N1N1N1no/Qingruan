"""Review phase-1 evidence without treating module results as whole-SoC ADP."""
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parents[1]
evidence = {}
cache = {}


def digest(path):
    path = path.resolve()
    if path not in cache:
        h = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                h.update(block)
        cache[path] = h.hexdigest()
    return cache[path]


def local(name, directory=None):
    if name.startswith('/workspace/'):
        return BASE / name[11:]
    path = Path(name)
    return path if path.is_absolute() else (directory or BASE) / path


def read(name):
    path = ROOT / name
    evidence[str(path)] = digest(path)
    return json.loads(path.read_text())


def verify_maps(data, directory):
    """Fixture/artifact names are relative to their own proof directory."""
    for key in ('source_sha256', 'fixture_sha256', 'artifact_sha256'):
        for name, want in data.get(key, {}).items():
            path = local(name, directory if key != 'source_sha256' else None)
            assert path.is_file() and digest(path) == want, str(path)


baseline = {}
stage = json.loads((BASE / 'native/swin-tiny/stage-status.json').read_text())
evidence[str(BASE / 'native/swin-tiny/stage-status.json')] = digest(BASE / 'native/swin-tiny/stage-status.json')
evidence[str(Path(__file__).resolve())] = digest(Path(__file__))
space = read('design-space.json')
for table in (stage['source_sha256'], stage['competition_sha256'], space['source_sha256']):
    for name, want in table.items():
        assert name not in baseline or baseline[name] == want, name
        baseline[name] = want
for name, want in baseline.items():
    assert digest(local(name)) == want, name
assert len(baseline) == 67
default_backend = ROOT / 'rtl/axi_accel_backend.sv'
assert digest(default_backend) == '5e0589f9f9c9f2d62cf128f36a5e3a85c1a261f9017689143b559299acb566c6'

arrays = read('work/array-synthesis/summary.json')
assert len(arrays) == 10 and len({x['id'] for x in arrays}) == 10
for entry in arrays:
    assert entry['status'] == 'PASS' and entry['clock_period_ns'] == 10
    case = ROOT / 'work/array-synthesis' / entry['id']
    log = (case / 'synthesis.log').read_text(errors='replace')
    assert not re.search(r'^Error', log, re.M) and 'AFFINE_ARRAY_SYNTHESIS_PASS' in log and 'Thank you' in log
    area = float(re.search(r'Total cell area:\s+([\d.]+)', (case / 'area.rpt').read_text())[1])
    assert abs(area - entry['area']) < 0.001
    assert (case / 'mapped.ddc').stat().st_size > 0
functional = read('work/array-functional/summary.json')
assert len(functional) == 10 and all(x['status'] == 'PASS' for x in functional)
controllers = read('work/controller-sweep/summary.json')
assert controllers['status'] == 'PASS' and len(controllers['completed']) == 10
array_comparison = read('array-module-comparison.json')
assert array_comparison['synthesis_configurations'] == array_comparison['benchmark_configurations'] == 10
verify_maps(array_comparison, ROOT)

backend = read('backend-module-comparison.json')
assert not backend['pending'] and len(backend['completed']) == 6
for entry in backend['completed']:
    proof_path = local(entry['proof'])
    proof = read(str(proof_path))
    assert proof['status'] == 'MAPPED_UNIT_SETUP_PASS'
    assert proof['clock_period_ns'] == 10 and proof['input_output_delay_ns'] == 1
    assert proof['setup_slack_ns'] >= 0 and proof['hold_slack_ns'] >= 0
    verify_maps(proof, proof_path.parent)
pool = read('work/backend-fp-pool/summary.json')
assert pool['status'] == 'PASS' and len(pool['comparisons']) == 9
assert all(x['identical_engine_cycles'] for x in pool['comparisons'])
verify_maps(pool, ROOT / 'work/backend-fp-pool')

rv32 = []
for identifier, directory in [('R8_C16_L4', 'complete-soc-R8_C16_L4'),
                              ('R8_C32_L4', 'complete-soc'),
                              ('R16_C8_L4', 'complete-soc-R16_C8_L4')]:
    proof = read('work/' + directory + '/summary.json')
    assert proof['status'] == 'PASS' and proof['identical_engine_cycles']
    assert len(proof['completed']) == 2
    binaries = []
    for mode in ('private', 'shared'):
        case = ROOT / 'work' / directory / mode
        item = read(str(case / 'summary.json'))
        assert item['status'] == 'PASS' and item['negative_control_detected'] and not item['full_model']
        assert len(item['engine_trace']) == 22
        verify_maps(item, case)
        assert digest(case / 'simv') == item['simulator_sha256']
        binaries.append(digest(case / 'program.bin'))
    assert binaries[0] == binaries[1]
    rv32.append({'id': identifier, 'commands_per_mode': 22, 'FP32_cycles_equal': True,
                 'same_firmware_binary_in_both_modes': True, 'negative_control_detected': True})

software = {}
for key, path in [('nonmac_addresses', 'work/nonmac-plan/summary.json'),
                  ('mac_addresses', 'work/mac-plan/summary.json'),
                  ('aux_host_numeric', 'work/aux-host/summary.json')]:
    proof = read(path)
    verify_maps(proof, (ROOT / path).parent)
    software[key] = {k: v for k, v in proof.items() if k not in ('source_sha256', 'scope')}
assert software['nonmac_addresses']['total_commands'] == 2584
assert software['mac_addresses']['MAC_nodes'] == 77
assert software['aux_host_numeric']['status'] == 'PASS' and software['aux_host_numeric']['nodes'] == 82
assert not software['aux_host_numeric']['hardware_executed']

pending = []
current_c_regressions = []
authorization_path = ROOT / 'native-validation-authorization.json'
execution_authorized = read(str(authorization_path)).get('received', False) if authorization_path.exists() else False
for directory, purpose in [('aux-soc', '新增 Slice/Concat/AvgPool 的真实 RV32/RTL 测试及损坏输出对照'),
                            ('planner-soc', '更新 C 库后的七节点 RV32 调度回归'),
                            ('mac-plan-soc', '更新 C 库后的原始 MAC 输入及后处理 RV32 回归')]:
    path = ROOT / 'work' / directory / 'summary.json'
    if not path.is_file():
        pending.append(purpose)
        preparation = read(str(path.parent / 'preparation.json'))
        assert preparation['status'] == 'FIRMWARE_AND_TEST_FIXTURES_READY_EXECUTION_PENDING'
        verify_maps(preparation, path.parent)
        current_c_regressions.append({'test': directory, 'status': 'EXECUTION_PENDING',
                                     'firmware_and_fixtures_prepared': True,
                                     'firmware_bytes': (path.parent / 'program.bin').stat().st_size})
        continue
    proof = read(str(path))
    assert proof['status'] == 'PASS'
    verify_maps(proof, path.parent)
    assert digest(path.parent / 'simv') == proof['simulator_sha256']
    current_c_regressions.append({'test': directory, 'status': 'PASS_CURRENT_SOURCE'})
firmware = []
for identifier in ('R8_C16_L4', 'R8_C32_L4', 'R16_C8_L4'):
    path = ROOT / 'work/complete-model-direct-build' / identifier / 'manifest.json'
    proof = read(str(path))
    verify_maps(proof, path.parent)
    assert proof['status'] == 'ALL8_RV32_FIRMWARE_BUILD_PASS_EXECUTION_PENDING' and not proof['full_RV32_executed']
    assert digest(path.parent / 'program.elf') == proof['firmware_sha256']
    assert (path.parent / 'program.bin').stat().st_size == proof['firmware_bytes']
    firmware.append({'id': identifier, 'bytes': proof['firmware_bytes'], 'executed': False})

shared = [x for x in backend['completed'] if x['shared_FP32']]
primary = min(shared, key=lambda x: x['module_testset_area_cycle_product'])
assert primary['id'] == 'R8_C16_L4'
comparison = []
for item in shared:
    comparison.append({k: item[k] for k in ('id', 'area', 'module_test_cycles_12', 'setup_slack_ns', 'hold_slack_ns')} |
                      {'area_change_vs_primary_percent': (item['area'] / primary['area'] - 1) * 100,
                       'MAC_sample_cycles_change_vs_primary_percent': (item['module_test_cycles_12'] / primary['module_test_cycles_12'] - 1) * 100})
status = 'PHASE1_REVIEW_PENDING_CURRENT_C_RV32_REGRESSIONS' if pending else 'PHASE1_MODULE_ENGINEERING_ACCEPTANCE_READY'
summary = {'status': status, 'updated_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
           'phase1_accepted': not pending, 'baseline_files_checked': len(baseline),
           'baseline_sources_unchanged': True, 'default_backend_unchanged': True,
           'array_configurations_mapped': 10, 'backend_configurations_mapped': 6,
           'primary_engineering_candidate': 'R8_C16_L4/shared',
           'selection_basis': 'minimum measured backend-area times finite 12-MAC-module-sample cycles among three fully mapped shortlist geometries; not full-SoC ADP or a global optimum',
           'shared_backend_comparison': comparison, 'RV32_backend_interleaving': rv32,
           'software': software, 'current_C_RV32_regressions': current_c_regressions,
           'firmware_builds': firmware, 'pending_phase1': pending,
           'native_validation_authorized': execution_authorized,
           'phase2_started': False, 'whole_model_accuracy_accepted': False, 'final_SoC_selection': 'PENDING_PHASE2',
           'official_preprocess_provenance': 'User reports no edits, origin uncertain; unchanged local ZIP member match does not authenticate organizer-hosted origin.',
           'source_sha256': evidence,
           'scope': 'Phase1 engineering review only; measured module area and finite module latency cannot replace organizer accuracy, complete RV32 inference, complete SoC area/latency/STA or gate/SDF deliverables.'}
(ROOT / 'phase1-review.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
review_state = '仍需完成下表中的第一阶段 RV32 回归' if pending else '当前源码的三项 RV32 回归均通过，第一阶段模块工程验收已具备证据'
lines = ['# 第一阶段工程选型与验收缺口', '',
         '主候选为 **8×16×4 阵列、128 个原 SRAM 宏、共享一套 FP32**。全部 10 种阵列和 6 个短名单后端的映射已完成。选择依据是当前有限模块样本的面积与周期；' + review_state + '，不能宣布已完成最终整机优化。', '',
         '|共享 FP32 候选|后端面积|12 个 MAC 样本周期|相对主候选面积|相对主候选周期|',
         '|---|---:|---:|---:|---:|']
for item in comparison:
    lines.append('|%s|%.6f|%d|%+.2f%%|%+.2f%%|' % (item['id'], item['area'], item['module_test_cycles_12'], item['area_change_vs_primary_percent'], item['MAC_sample_cycles_change_vs_primary_percent']))
lines += ['', '后端面积含阵列、DMA 及全部八引擎，不含 CPU/MMIO/router。周期仅为同条件 12 个 MAC 行块，未含完整非 MAC、软件调度或整图推理；乘积仅用于第一阶段工程取舍。8×32×4 保留为较高吞吐的第二阶段对照，16×8×4 保留其测量及功能证据。', '',
          '共享 FP32 在三个几何下分别减少约 1.15%、0.65%、1.09% 后端面积。18 组模块对照及三个几何的实际 RV32 测试中，独立/共享逐命令引擎周期相同；各几何两种资源配置使用同一固件二进制，并通过故意破坏输出的检出测试。', '',
          'SRAM、浮点核、总线均保留原数值语义。LayerNorm/Softmax/向量复用 PE 缓存；当前调度串行占用资源，因此共享浮点核没有在上述样本增加周期。这不能推定未来并行调度也没有代价。10ns/1ns IO 的后端 setup 余量很小，主候选为 +0.000454903ns；只说明该模块 DC 约束通过，完整 SoC、门级/SDF 与最终时序仍待验收。', '',
          '阵列样本的约 98% 是形状填充效率。计入 DMA/控制/等待后的有效峰值利用率，8×16×4 为 4.569%、8×32×4 为 3.267%；继续增加乘法通道收益受搬运和控制限制。8×32×8 相对 8×32×4 的 12 个样本周期只减少约 2.93%，阵列自身面积增加约 29.60%。这些都是模块样本结论。', '',
          'C 命令生成器已对目标图的 2584 条描述完成地址核对：含全部 77 个 MAC 的 1877 批后处理，以及 58 个浮点 Slice、23 个浮点 Concat、1 个 AvgPool。MAC 的原始 A/B 地址覆盖 45367440 个元素，不再依赖 CPU 逐元素打包。只有 3 个整数形状元数据节点保留 CPU，输出共 72 字节。', '',
          '新增 82 个 Slice/Concat/AvgPool 节点在主机上按不变的 graph.c 逐位对照，通过 161 条组合命令、28001280 个最终输出字节及写入护栏检查。AvgPool 保留从 +0 开始的逐项 FP32 加法顺序，再做 FP32 除法。该检查未执行 RTL、图像或整网。', '',
          '|第一阶段剩余检查|状态|', '|---|---|']
for item, (_, purpose) in zip(current_c_regressions, [('aux-soc', '新增 Slice/Concat/AvgPool 的真实 RV32/RTL 测试及损坏输出对照'), ('planner-soc', '更新 C 库后的七节点 RV32 调度回归'), ('mac-plan-soc', '更新 C 库后的原始 MAC 输入及后处理 RV32 回归')]):
    lines.append('|%s|%s|' % (purpose, '通过，已核对当前源码' if item['status'] == 'PASS_CURRENT_SOURCE' else '待运行；当前固件和夹具已准备'))
if pending:
    execution_state = ('用户已明确允许继续本地验证，三项 VCS 测试已启动；已完成的两项回归通过，新增算子测试的结果以实际终态为准。' if execution_authorized else '三项回归的固件与 RTL 测试夹具均已准备好并核对当前源码哈希，只编译链接、未执行 RTL。启动本机 VCS 的自动权限审批连续两次超时，工具未创建测试进程；已向用户请求允许继续。')
else:
    execution_state = '三项回归的当前源文件、固件、夹具与模拟器哈希已核对；当前源码的实际 RTL 测试通过。'
lines += ['', execution_state + '原七节点和原始 MAC 测试的旧证明保留在归档目录，未将旧 PASS 扩大到新版本。', '',
          '三种短名单整图 RV32 固件均已构建为 52592 字节，含新算子组合；只编译、未执行模型。原工程、模型、预处理、标签与 GUI 默认入口保留，67 个冻结基线文件的 SHA256 已重新核对。', '',
          '第二阶段在第一阶段验收后开始：真实 RV32 完整推理、公开参考精度及数学库差异处理；同库/时钟/内存下的完整 SoC 面积、总周期与 ADP；STA、门级/SDF 及飞书要求的交付物。公开参考仍为 3/5 匹配，不能以改标签、预处理或容差通过。用户确认未改预处理脚本，来源仍未确认。', '',
          '可复查记录：`phase1-review.json`、`阵列模块比较.md`、`后端模块比较.md`、`nonmac-cache-comparison.json` 和各 `work/*/summary.json`。']
(ROOT / '第一阶段工程选型.md').write_text('\n'.join(lines) + '\n')
print(status, 'arrays=10 backends=6 baseline=67 current_RV32_regressions_pending=' + str(len(pending)))
