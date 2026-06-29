from io import StringIO

from tablib.formats._yaml import YAMLFormat


def test_haao_r102_yaml_detect_rejects_unsupported_tag_without_raising():
    stream = StringIO("!!python/object:os.system {}")

    assert YAMLFormat.detect(stream) is False
