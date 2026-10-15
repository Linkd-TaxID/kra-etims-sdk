"""The device helper never prints credentials or arbitrary upstream data."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("init_device", Path(__file__).parent.parent / "init_device.py")
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


@pytest.mark.parametrize("failure", [False, True])
def test_handshake_output_redacts_upstream(monkeypatch, capsys, failure):
    secret = "test-sensitive-device-material"
    monkeypatch.setenv("TAXID_API_KEY", secret)

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["api_key"] == secret

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def initialize_device_handshake(self):
            if failure:
                raise RuntimeError(secret)
            return {"cmcKey": secret, "data": {"receiptSignature": secret}}

    monkeypatch.setattr(helper, "KRAeTIMSClient", Client)
    if failure:
        with pytest.raises(SystemExit, match="1"):
            helper.run_init_device()
    else:
        helper.run_init_device()
    output = capsys.readouterr()
    assert secret not in output.out + output.err
    expected = "Handshake failed" if failure else "Handshake request completed"
    assert expected in output.out


def test_pos_demo_requires_explicit_seller_pin(monkeypatch):
    import runpy
    monkeypatch.setenv("TAXID_API_KEY", "test-key")
    monkeypatch.delenv("SANDBOX_TIN", raising=False)
    script = Path(__file__).parent.parent / "examples/pos_basket_demo.py"
    with pytest.raises(SystemExit, match="Set SANDBOX_TIN"):
        runpy.run_path(str(script), run_name="__main__")
