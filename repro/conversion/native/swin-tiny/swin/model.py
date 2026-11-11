"""Read the official model and derive every MAC mapping from its Q/DQ graph."""
from collections import Counter
import hashlib
import math
from pathlib import Path

import numpy as np
import onnx
from onnx import numpy_helper, helper
from onnxruntime.tools.symbolic_shape_infer import SymbolicShapeInference

MODEL_FILE = 'swin_tiny_patch4_window7_224_imagenet1k_int8.onnx'


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def attrs(node):
    return {a.name: helper.get_attribute_value(a) for a in node.attribute}


class Model:
    def __init__(self, competition):
        self.competition = Path(competition)
        self.path = self.competition / 'models' / MODEL_FILE
        self.original = onnx.load(self.path)
        onnx.checker.check_model(self.original)
        self.model = SymbolicShapeInference.infer_shapes(self.original, auto_merge=True, verbose=0)
        self.nodes = list(self.model.graph.node)
        self.initializers = {t.name: numpy_helper.to_array(t) for t in self.model.graph.initializer}
        self.constants = self.initializers.copy()
        for n in self.nodes:
            if n.op_type == 'Constant':
                a = attrs(n)
                self.constants[n.output[0]] = numpy_helper.to_array(a['value'])
        self.producer = {o: n for n in self.nodes for o in n.output}
        self.shapes = {}
        self.dtypes = {}
        for v in list(self.model.graph.input) + list(self.model.graph.output) + list(self.model.graph.value_info):
            dimensions = [d.dim_value if d.HasField('dim_value') else d.dim_param for d in v.type.tensor_type.shape.dim]
            if any(not isinstance(d, int) or d < 0 for d in dimensions):
                raise ValueError('Unresolved tensor shape: ' + v.name + ' ' + str(dimensions))
            self.shapes[v.name] = dimensions
            self.dtypes[v.name] = v.type.tensor_type.elem_type
        for name, array in self.constants.items():
            self.shapes[name] = list(array.shape)
            self.dtypes[name] = helper.np_dtype_to_tensor_dtype(array.dtype)

    def qparams(self, name):
        node = self.producer.get(name)
        if node is None or node.op_type != 'DequantizeLinear':
            raise ValueError('MAC operand is not a Q/DQ tensor: ' + name)
        raw, scale, zp = node.input
        s, z = self.constants[scale], self.constants[zp]
        dtype = np.dtype(helper.tensor_dtype_to_np_dtype(self.dtypes[raw]))
        limits = np.iinfo(dtype)
        return {'tensor': raw, 'dq_node': node.name, 'scale': scale, 'zero_point': zp,
                'scale_shape': list(s.shape), 'zero_point_shape': list(z.shape),
                'axis': attrs(node).get('axis', 1), 'dtype': str(dtype),
                'centered_min': int(limits.min - int(z.max())),
                'centered_max': int(limits.max - int(z.min())),
                'static': raw in self.constants}

    def mapping(self):
        mapped = []
        for index, node in enumerate(self.nodes):
            if node.op_type not in {'Conv', 'MatMul', 'Gemm'}:
                continue
            a, b = (self.qparams(name) for name in node.input[:2])
            ash, bsh = (self.shapes[name] for name in node.input[:2])
            attr = attrs(node)
            if a['scale_shape']:
                raise ValueError('Per-axis activation MAC needs an explicit K-scale lowering: ' + node.name)
            if node.op_type == 'Conv':
                if attr.get('group', 1) != 1:
                    raise ValueError('Grouped convolution needs its own mapping')
                output = self.shapes[node.output[0]]
                batches, m, n, k = 1, output[0] * math.prod(output[2:]), bsh[0], math.prod(bsh[1:])
                b_axis = 0
                method = 'im2col: output position -> lane, output channel -> column, C/kh/kw -> row'
            elif node.op_type == 'Gemm':
                if attr.get('transA', 0) or float(attr.get('alpha', 1)) != 1 or float(attr.get('beta', 1)) != 1:
                    raise ValueError('Unsupported Gemm attributes: ' + str(attr))
                batches, m, k = 1, ash[-2], ash[-1]
                trans = attr.get('transB', 0)
                n, b_axis = (bsh[-2], len(bsh)-2) if trans else (bsh[-1], len(bsh)-1)
                method = 'classifier: input row -> lane; class -> column'
            else:
                k, n = ash[-1], bsh[-1]
                if k != bsh[-2]:
                    raise ValueError('MatMul K mismatch')
                batch = np.broadcast_shapes(tuple(ash[:-2]), tuple(bsh[:-2]))
                batches, m = math.prod(batch), ash[-2]
                if len(bsh) == 2:
                    m, batches = batches*m, 1
                b_axis = len(bsh)-1
                method = ('shared weight: flatten leading A dimensions into M' if len(bsh) == 2 else
                          'dynamic attention: preserve window/head batch, stream runtime B weights')
            if b['scale_shape'] and b['axis'] % len(bsh) != b_axis:
                raise ValueError('Per-K B scale cannot be factored out: ' + node.name)
            if max(abs(a['centered_min']), abs(a['centered_max']), abs(b['centered_min']), abs(b['centered_max'])) > 255:
                raise ValueError('Operand requires more than the two-plane affine-INT8 lowering')
            bound = k * max(abs(a['centered_min']),abs(a['centered_max'])) * max(abs(b['centered_min']),abs(b['centered_max']))
            if bound > np.iinfo(np.int32).max:
                raise ValueError('Accumulator requires a wider contract: ' + node.name)
            tiles = batches * math.ceil(m/4) * math.ceil(n/16) * math.ceil(k/16)
            mapped.append({'node_index': index, 'name': node.name, 'op': node.op_type,
                           'inputs': list(node.input), 'output': node.output[0],
                           'input_shapes': [ash,bsh], 'output_shape': self.shapes[node.output[0]],
                           'batch': batches, 'M': m, 'N': n, 'K': k, 'MACs': batches*m*n*k,
                           'matrix_tiles': tiles, 'pe_passes_max': tiles*4,
                           'A': a, 'B': b, 'attributes': attr, 'mapping': method,
                           'int32_accumulator_bound': bound,
                           'tail_padding': {'M': (-m)%4, 'N':(-n)%16, 'K':(-k)%16}})
        return mapped

    def analysis(self):
        macs = self.mapping()
        counts = Counter(n.op_type for n in self.nodes)
        return {'model': MODEL_FILE, 'sha256': sha(self.path), 'opset': 17,
                'input': {'name':'input','shape':self.shapes['input'],'dtype':'float32'},
                'output': {'name':'output','shape':self.shapes['output'],'dtype':'float32'},
                'nodes':len(self.nodes),'initializers':len(self.initializers),'operators':dict(counts),
                'MAC_nodes':len(macs),'MACs':sum(n['MACs'] for n in macs),
                'PE':{'rows':16,'columns':16,'lanes':4,'weight_addresses':16,
                      'tile_MNK':[4,16,16],'raw_code':'8-bit sign-magnitude, canonical zero',
                      'affine_lowering':'x = lo + 128*hi; lo=((x+64)%128)-64; hi=(x-lo)//128; 4 plane pairs; no clipping'},
                'preprocess_sha256':sha(self.competition/'official_preprocess.py'),
                'stages':[{'tokens':3136,'channels':96,'blocks':2,'heads':3},
                          {'tokens':784,'channels':192,'blocks':2,'heads':6},
                          {'tokens':196,'channels':384,'blocks':6,'heads':12},
                          {'tokens':49,'channels':768,'blocks':2,'heads':24}],
                'MAC_mapping':macs,
                'cpu_ops':[name for name in counts if name not in {'Conv','MatMul','Gemm'}],
                'all_MACs_mapped':len(macs)==sum(counts[t] for t in ['Conv','MatMul','Gemm'])}
