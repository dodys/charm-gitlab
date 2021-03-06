#!/usr/bin/python3
"""Configure testing session for unit testing charm."""
import sys

import yaml
import pytest

import mock

from charmhelpers.core import unitdata

sys.modules["charms.layer"] = mock.Mock()
sys.modules["reactive"] = mock.Mock()
sys.modules["reactive.layer_backup"] = mock.Mock()


@pytest.fixture
def mock_layers(monkeypatch):
    """Mock charm layer inclusion."""
    mock_layer_backup = mock.Mock()

    monkeypatch.setattr("libgitlab.BackupHelper", mock_layer_backup)
    return {"layer_backup": mock_layer_backup}


@pytest.fixture
def mock_hookenv_config(monkeypatch):
    """Mock charm hook environment items like charm configuration."""

    def mock_config():
        cfg = {}
        yml = yaml.safe_load(open("./config.yaml"))

        # Load all defaults
        for key, value in yml["options"].items():
            cfg[key] = value["default"]

        # Manually add cfg from other layers
        # cfg['my-other-layer'] = 'mock'
        return cfg

    monkeypatch.setattr("libgitlab.hookenv.config", mock_config)


@pytest.fixture
def mock_remote_unit(monkeypatch):
    """Mock the remote unit name for charm in test."""
    monkeypatch.setattr("libgitlab.hookenv.remote_unit", lambda: "unit-mock/0")


@pytest.fixture
def mock_charm_dir(monkeypatch):
    """Mock the charm directory for charm in test."""
    monkeypatch.setattr("libgitlab.hookenv.charm_dir", lambda: ".")


@pytest.fixture
def mock_open_port(monkeypatch):
    """Mock the call to open ports."""
    mocked_open_port = mock.Mock()
    monkeypatch.setattr("libgitlab.hookenv.open_port", mocked_open_port)
    return mocked_open_port


@pytest.fixture
def mock_close_port(monkeypatch):
    """Mock the call to close ports."""
    mocked_close_port = mock.Mock()
    monkeypatch.setattr("libgitlab.hookenv.close_port", mocked_close_port)
    return mocked_close_port


@pytest.fixture
def mock_opened_ports(monkeypatch):
    """Mock the call to get opened ports."""

    def mock_opened_ports():
        return ["2222/tcp", "80/tcp", "443/tcp"]

    mocked_opened_ports = mock.Mock()
    mocked_opened_ports.side_effect = mock_opened_ports
    monkeypatch.setattr("libgitlab.hookenv.opened_ports", mocked_opened_ports)
    return mocked_opened_ports


@pytest.fixture
def mock_get_installed_version(monkeypatch):
    """Mock the installed version."""
    installed_version = mock.Mock()
    installed_version.return_value = "1.1.1"
    monkeypatch.setattr(
        "libgitlab.GitlabHelper.get_installed_version", installed_version
    )


@pytest.fixture
def mock_get_latest_version(monkeypatch):
    """Mock get_latest_version."""
    latest_version = mock.Mock()
    latest_version.return_value = "1.1.1"
    monkeypatch.setattr("libgitlab.GitlabHelper.get_latest_version", latest_version)


@pytest.fixture
def mock_upgrade_package(
    mock_get_installed_version, mock_get_latest_version, monkeypatch
):
    """Mock the upgrade_package function and set the installed versions.

    When a wildcard is provided the minor and patch are set to 1
    """

    def mock_upgrade(self, version=None):
        if version:
            sane_version = version.replace("*", "1.1")
            self.get_installed_version.return_value = sane_version
        else:
            self.get_installed_version.return_value = self.get_latest_version()

    monkeypatch.setattr("libgitlab.GitlabHelper.upgrade_package", mock_upgrade)


@pytest.fixture
def mock_gitlab_hookenv_log(monkeypatch):
    """Mock hookenv.log."""
    mock_log = mock.Mock()
    monkeypatch.setattr("libgitlab.hookenv.log", mock_log)
    return mock_log


@pytest.fixture
def mock_gitlab_host(monkeypatch):
    """Mock host import on libgitlab."""
    mock_host = mock.Mock()
    monkeypatch.setattr("libgitlab.host", mock_host)
    return mock_host


@pytest.fixture
def mock_gitlab_get_flag_value(monkeypatch):
    """Mock _get_flag_value on libgitlab."""
    mock_flag_value = mock.Mock()
    mock_flag_value.return_value = None
    monkeypatch.setattr("libgitlab._get_flag_value", mock_flag_value)
    return mock_flag_value


@pytest.fixture
def mock_gitlab_socket(monkeypatch):
    """Mock socket import on libgitlab."""
    mock_socket = mock.Mock()
    mock_socket.getfqdn = mock.Mock()
    mock_socket.getfqdn.return_value = "mock.example.com"
    monkeypatch.setattr("libgitlab.socket", mock_socket)
    return mock_socket


@pytest.fixture
def mock_apt_install(monkeypatch):
    """Mock the charmhelper fetch apt_install method."""
    mocked_apt_install = mock.Mock(returnvalue=True)
    monkeypatch.setattr("libgitlab.apt_install", mocked_apt_install)
    return mocked_apt_install


@pytest.fixture
def mock_apt_update(monkeypatch):
    """Mock the charmhelpers fetch apt_update method."""
    mocked_apt_update = mock.Mock(returnvalue=True)
    monkeypatch.setattr("libgitlab.apt_update", mocked_apt_update)
    return mocked_apt_update


@pytest.fixture
def mock_add_source(monkeypatch):
    """Mock the charmhelpers fetch add_source method."""

    def print_add_source(line, key):
        print("Mocked add source: {} ({})".format(line, key))
        return True

    mocked_add_source = mock.Mock()
    mocked_add_source.get.side_effect = print_add_source
    monkeypatch.setattr("libgitlab.add_source", mocked_add_source)
    return mocked_add_source


@pytest.fixture
def mock_gitlab_subprocess(monkeypatch):
    """Mock subprocess import on libgitlab."""
    mock_subprocess = mock.Mock()
    monkeypatch.setattr("libgitlab.subprocess", mock_subprocess)
    return mock_subprocess


@pytest.fixture
def mock_template(monkeypatch):
    """Mock the file permission modification syscalls used by the templating library."""
    monkeypatch.setattr("libgitlab.templating.host.os.fchown", mock.Mock())
    monkeypatch.setattr("libgitlab.templating.host.os.chown", mock.Mock())
    monkeypatch.setattr("libgitlab.templating.host.os.fchmod", mock.Mock())


@pytest.fixture
def mock_unit_db(monkeypatch):
    """Mock the key value store."""
    mock_kv = mock.Mock()
    mock_kv.return_value = unitdata.Storage(path=":memory:")
    monkeypatch.setattr("libgitlab.unitdata.kv", mock_kv)


@pytest.fixture
def libgitlab(
    tmpdir,
    mock_layers,
    mock_hookenv_config,
    mock_charm_dir,
    mock_upgrade_package,
    mock_gitlab_socket,
    mock_apt_install,
    mock_apt_update,
    mock_add_source,
    mock_template,
    mock_gitlab_subprocess,
    mock_unit_db,
    mock_open_port,
    mock_close_port,
    mock_opened_ports,
    monkeypatch,
):
    """Mock important aspects of the charm helper library for operation during unit testing."""
    from libgitlab import GitlabHelper

    gitlab = GitlabHelper()

    # Example config file patching
    cfg_file = tmpdir.join("example.cfg")
    with open("./tests/unit/example.cfg", "r") as src_file:
        cfg_file.write(src_file.read())
    gitlab.example_config_file = cfg_file.strpath

    commands_file = tmpdir.join("commands.load")
    gitlab.gitlab_commands_file = commands_file.strpath
    config_file = tmpdir.join("gitlab.rb")
    gitlab.gitlab_config = config_file.strpath

    # Mock host functions not appropriate for unit testing
    gitlab.fetch_gitlab_apt_package = mock.Mock()
    gitlab.gitlab_reconfigure_run = mock.Mock()

    # Any other functions that load the helper will get this version
    monkeypatch.setattr("libgitlab.GitlabHelper", lambda: gitlab)

    return gitlab
