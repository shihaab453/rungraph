"""Generate the checked-in real TensorBoard event fixtures.

Run with a venv that has ``tensorboard==2.20.0`` installed:

    python generate.py OUTDIR

The writer below is TensorBoard's own ``EventFileWriter`` (its protos, its
TFRecord framing and CRC), so the fixtures are not produced by the reader's
test builder. The output is byte-frozen by
``tests/test_tensorboard_real_fixtures.py``; regenerate only with a recorded
reason.
"""

from __future__ import annotations

import math
import os
import struct
import sys
from pathlib import Path

from tensorboard.compat.proto.event_pb2 import Event
from tensorboard.compat.proto.graph_pb2 import GraphDef
from tensorboard.compat.proto.node_def_pb2 import NodeDef
from tensorboard.compat.proto.summary_pb2 import Summary
from tensorboard.compat.proto.tensor_pb2 import TensorProto
from tensorboard.compat.proto.tensor_shape_pb2 import TensorShapeProto
from tensorboard.compat.proto.types_pb2 import DataType
from tensorboard.summary.writer.event_file_writer import EventFileWriter


def scalar(tag: str, value: float) -> Summary.Value:
    return Summary.Value(tag=tag, simple_value=value)


def tensor(
    tag: str, dtype: int, content: bytes | None = None,
    shape: list[int] | None = None, *, float_val=None, double_val=None,
) -> Summary.Value:
    proto = TensorProto(dtype=dtype)
    if shape is not None:
        proto.tensor_shape.CopyFrom(
            TensorShapeProto(dim=[TensorShapeProto.Dim(size=n) for n in shape])
        )
    if content is not None:
        proto.tensor_content = content
    if float_val is not None:
        proto.float_val.extend(float_val)
    if double_val is not None:
        proto.double_val.extend(double_val)
    return Summary.Value(tag=tag, tensor=proto)


def write(path: Path, events: list[Event]) -> None:
    folder = Path("/tmp/rg-fixture-writer")
    for old in folder.glob("*"):
        old.unlink()
    folder.mkdir(parents=True, exist_ok=True)
    writer = EventFileWriter(str(folder))
    for event in events:
        writer.add_event(event)
    writer.close()
    (name,) = [p for p in folder.iterdir() if p.is_file()]
    path.write_bytes(name.read_bytes())


def legacy(path: Path) -> None:
    write(path, [
        Event(file_version="brain.Event:2"),
        Event(step=1, summary=Summary(value=[scalar("loss", 0.5), scalar("accuracy", 0.75)])),
        Event(step=2, summary=Summary(value=[scalar("loss", 0.25)])),
        Event(step=3, summary=Summary(value=[scalar("loss", math.nan)])),
        Event(step=4, summary=Summary(value=[scalar("inf", math.inf), scalar("neg_inf", -math.inf)])),
    ])


def tensors(path: Path) -> None:
    write(path, [
        Event(file_version="brain.Event:2"),
        Event(step=10, summary=Summary(value=[
            tensor("f32_content", DataType.DT_FLOAT, struct.pack("<f", 0.25)),
            tensor("f64_content", DataType.DT_DOUBLE, struct.pack("<d", 0.1)),
            tensor("f32_rank1", DataType.DT_FLOAT, struct.pack("<f", 1.5), shape=[1]),
            tensor("f64_packed", DataType.DT_DOUBLE, shape=[], double_val=[0.2]),
            tensor("f32_packed", DataType.DT_FLOAT, shape=[], float_val=[0.3]),
        ])),
        Event(step=11, summary=Summary(value=[
            tensor("f32_2d", DataType.DT_FLOAT, struct.pack("<ff", 0.5, 0.5), shape=[1, 1]),
            tensor("int_scalar", DataType.DT_INT32, struct.pack("<i", 7)),
            tensor("half_scalar", DataType.DT_HALF, b"\x00\x3c"),
        ])),
    ])


def graph(path: Path) -> None:
    node = NodeDef(name="input", op="Placeholder")
    write(path, [
        Event(file_version="brain.Event:2"),
        Event(graph_def=GraphDef(node=[node]).SerializeToString()),
        Event(step=7, summary=Summary(value=[scalar("loss", 0.9)])),
        Event(step=8, summary=Summary(value=[scalar("loss", 0.95)])),
    ])


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/rg-real-fixtures")
    out.mkdir(parents=True, exist_ok=True)
    legacy(out / "legacy-simple-value.tfevents")
    tensors(out / "tensor-content.tfevents")
    graph(out / "graph-record.tfevents")
    for path in sorted(out.iterdir()):
        import hashlib
        print(path.name, len(path.read_bytes()), hashlib.sha256(path.read_bytes()).hexdigest())


if __name__ == "__main__":
    main()
