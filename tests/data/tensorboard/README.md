# Real TensorBoard event fixtures

These files were written by TensorBoard's own `EventFileWriter` from
`tensorboard==2.20.0` on Python 3.12, not by RunEvidence's test builder. They
give the reader an oracle that does not share an implementation with it:
TensorBoard produced the protobufs, the TFRecord framing and the CRC32C.

Provenance:

| File | Bytes | SHA-256 | Contents |
| --- | ---: | --- | --- |
| `legacy-simple-value.tfevents` | 283 | `21b6528ac0132b313c6bb506d9b3cd56bfe4992bee493c424132582c1dbf1ffe` | file version; `loss` steps 1/2/3 (0.5, 0.25, NaN); `accuracy` step 1; `inf`/`neg_inf` step 4 |
| `tensor-content.tfevents` | 380 | `dfaf40263c49a35dd9f30ed81a57ac1821d9881bd5cc773bbb4aa58a6e249a07` | `DT_FLOAT`/`DT_DOUBLE` `tensor_content`, rank-1 `[1]`, packed `float_val`/`double_val`, plus a rank-2 `[1,1]`, an int and a half scalar that must be non-scalar/unsupported |
| `graph-record.tfevents` | 225 | `35aa71290a9258e1b0d0ee7804e937da8f20820f5d7ddb909de294f804cbd06a` | file version; a `GraphDef` event; `loss` at steps 7 and 8 |

`generate.py` (SHA-256
`8676ee835de974fd52522a8fcfff4f465644e046967a66e0a0496665f2c4873c`)
reproduces the three files with `tensorboard==2.20.0`:

```
python -m venv /tmp/rg-tbvenv
/tmp/rg-tbvenv/bin/pip install tensorboard==2.20.0
/tmp/rg-tbvenv/bin/python tests/data/tensorboard/generate.py OUTDIR
```

The fixture hashes are pinned by
[`tests/test_tensorboard_real_fixtures.py`](../../test_tensorboard_real_fixtures.py).
An intentional regeneration must update this table, the pins and the reason.
