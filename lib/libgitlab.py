"""The helper library for the GitLab charm.

Provides the majority of the logic in the charm, specifically all logic which
can be meaningfully unit tested.
"""
try:
    from urllib.parse import urlparse
except ImportError:
    from urlparse import urlparse

import socket
import subprocess

from charmhelpers.core import hookenv, host, templating, unitdata
from charmhelpers.fetch import apt_install, apt_update, add_source, ubuntu_apt_pkg

from charms.reactive.flags import _get_flag_value
from charms.reactive.helpers import any_file_changed

from reactive.layer_backup import Backup as BackupHelper

import semantic_version


class GitlabHelper:
    """The GitLab helper class.

    Provides helper functions and implements the actual logic of the GitLab
    charm. Used and called from the Reactive charm layer and unit tests.
    """

    package_name = "gitlab-ce"
    gitlab_config = "/etc/gitlab/gitlab.rb"

    def __init__(self):
        """Load hookenv key/value store and charm configuration."""
        self.charm_config = hookenv.config()
        if self.charm_config["version"]:
            self.version = self.charm_config["version"]
        else:
            self.version = None
        self.set_package_name(self.charm_config["package_name"])
        self.kv = unitdata.kv()
        self.gitlab_commands_file = "/etc/gitlab/commands.load"

    def set_package_name(self, name):
        """Parse and set the package name used to install and upgrade GitLab."""
        if name == "gitlab-ee":
            self.package_name = "gitlab-ee"
        else:
            self.package_name = "gitlab-ce"

    def restart(self):
        """Restart the GitLab service."""
        host.service_restart("gitlab")
        return True

    def get_external_uri(self):
        """Return the configured external URL from Charm configuration."""
        configured_value = self.charm_config["external_url"]
        if configured_value:
            return configured_value
        else:
            fqdn = "http://{}".format(socket.getfqdn())
            return fqdn

    def get_sshhost(self):
        """Return the host used when configuring SSH access to GitLab."""
        url = urlparse(self.get_external_uri())

        if url.hostname:
            return url.hostname
        else:
            return socket.getfqdn()

    def get_sshport(self):
        """Return the host used when configuring SSH access to GitLab."""
        if _get_flag_value("reverseproxy.configured"):
            return self.charm_config["proxy_ssh_port"]
        return self.charm_config["ssh_port"]

    def get_smtp_enabled(self):
        """Return True if all configuration is in place for external SMTP usage."""
        if self.charm_config.get("smtp_server") and self.charm_config.get("smtp_port"):
            return True
        return False

    def get_smtp_domain(self):
        """Return the SMTP domain based on configuration but default to the domain portion of the external host."""
        if self.charm_config.get("smtp_domain", None):
            return self.charm_config.get("smtp_domain")
        else:
            return self.get_sshhost()

    def configure_proxy(self, proxy):
        """Configure GitLab for operation behind a reverse proxy."""
        url = urlparse(self.get_external_uri())

        if url.scheme == "https":
            port = 443
        else:
            port = 80

        if self.charm_config["proxy_via_ip"]:
            networks = hookenv.network_get(proxy.relation_name)
            internal_host = networks["ingress-addresses"][0]
        else:
            internal_host = socket.getfqdn()

        proxy_config = [
            {
                "mode": "http",
                "external_port": port,
                "internal_host": internal_host,
                "internal_port": self.charm_config["http_port"],
                "subdomain": url.hostname,
            },
            {
                "mode": "tcp",
                "external_port": self.charm_config["proxy_ssh_port"],
                "internal_host": internal_host,
                "internal_port": self.charm_config["ssh_port"],
            },
        ]
        proxy.configure(proxy_config)

    def mysql_configured(self):
        """Determine if we have MySQL DB configuration present."""
        if (
            self.kv.get("mysql_host")
            and self.kv.get("mysql_port")
            and self.kv.get("mysql_db")
            and self.kv.get("mysql_user")
            and self.kv.get("mysql_pass")
        ):
            return True
        return False

    def legacy_db_configured(self):
        """Determine if we have legacy MySQL DB configuration present."""
        if (
            self.kv.get("db_host")
            and self.kv.get("db_port")
            and self.kv.get("db_db")
            and self.kv.get("db_user")
            and self.kv.get("db_pass")
        ):
            return True
        return False

    def install_pgloader(self):
        """Install pgloader for migrating DB from MySQL to PostgreSQL."""
        hookenv.status_set(
            "maintenance", "Installing and configuring pgloader to perform migration..."
        )
        hookenv.log("Installing pgloader...", hookenv.INFO)
        apt_install("pgloader", fatal=True)

    def configure_pgloader(self):
        """Render templated commands.load file for pgloader to self.gitlab_commands_file."""
        hookenv.log(
            "Rendering pgloader commands.load file to /etc/gitlab", hookenv.INFO
        )
        templating.render(
            "commands.load.j2",
            self.gitlab_commands_file,
            {
                "pgsql_host": self.kv.get("pgsql_host"),
                "pgsql_port": self.kv.get("pgsql_port"),
                "pgsql_database": self.kv.get("pgsql_db"),
                "pgsql_user": self.kv.get("pgsql_user"),
                "pgsql_password": self.kv.get("pgsql_pass"),
                "mysql_host": self.kv.get("mysql_host"),
                "mysql_port": self.kv.get("mysql_port"),
                "mysql_database": self.kv.get("mysql_db"),
                "mysql_user": self.kv.get("mysql_user"),
                "mysql_password": self.kv.get("mysql_pass"),
            },
        )
        if any_file_changed([self.gitlab_commands_file]):
            self.run_pgloader()

    def run_pgloader(self):
        """Run pgloader to migrate the data."""
        hookenv.log("Running pgloader", hookenv.INFO)
        self.render_config()
        # force a reconfigure as well
        self.gitlab_reconfigure_run()
        subprocess.check_output(
            ["/usr/bin/pgloader", self.gitlab_commands_file], stderr=subprocess.STDOUT
        )

    def mysql_migrated(self):
        """Return the contents of the mysql_migration_run KV entry which is set when migration completes."""
        if self.kv.get("mysql_migration_run"):
            return True
        else:
            return False

    def migrate_db(self):
        """Migrate DB contents from MySQL to PostgreSQL."""
        if self.mysql_configured() and self.pgsql_configured():
            hookenv.log("Migrating database from MySQL to PostgreSQL", hookenv.INFO)
            hookenv.status_set("maintenance", "Starting MySQL to PostgreSQL migration")
            hookenv.status_set("maintenance", "Ensuring pgloader is installed")
            self.install_pgloader()
            hookenv.status_set(
                "maintenance", "Rendering pgloader configuration for migration"
            )
            self.configure_pgloader()
            hookenv.status_set(
                "maintenance",
                "MySQL to PostgreSQL migration in progress via pgloader...",
            )
            self.run_pgloader()
            hookenv.log(
                "Migrated database from MySQL to PostgreSQL, running configure.",
                hookenv.INFO,
            )
            hookenv.status_set(
                "maintenance",
                "Please remove the MySQL relation now migration is complete.",
            )
            self.kv.set("mysql_migration_run", True)

    def migrate_mysql_config(self):
        """Migrate legacy MySQL configuration to new DB configuration in KV store."""
        if self.mysql_configured():
            self.kv.set("db_host", self.kv.get("mysql_host"))
            self.kv.set("db_port", self.kv.get("mysql_port"))
            self.kv.set("db_db", self.kv.get("mysql_db"))
            self.kv.set("db_user", self.kv.get("mysql_user"))
            self.kv.set("db_pass", self.kv.get("mysql_pass"))
            self.kv.set("db_adapter", "mysql2")

    def pgsql_configured(self):
        """Determine if we have all requried DB configuration present."""
        if (
            self.kv.get("pgsql_host")
            and self.kv.get("pgsql_port")
            and self.kv.get("pgsql_db")
            and self.kv.get("pgsql_user")
            and self.kv.get("pgsql_pass")
        ):
            hookenv.log(
                "PostgreSQL is related and configured in the charm KV store",
                hookenv.DEBUG,
            )
            return True
        return False

    def redis_configured(self):
        """Determine if Redis is related and the KV has been updated with configuration."""
        if self.kv.get("redis_host") and self.kv.get("redis_port"):
            hookenv.log(
                "Redis is related and configured in the charm KV store", hookenv.DEBUG
            )
            return True
        return False

    def remove_mysql_conf(self):
        """Remove legacy MySQL configuraion from the unit KV store."""
        # legacy kv to clean up
        self.kv.unset("mysql_host")
        self.kv.unset("mysql_port")
        self.kv.unset("mysql_db")
        self.kv.unset("mysql_user")
        self.kv.unset("mysql_pass")

    def remove_pgsql_conf(self):
        """Remove the MySQL configuration from the unit KV store."""
        self.kv.unset("db_host")
        self.kv.unset("db_port")
        self.kv.unset("db_db")
        self.kv.unset("db_user")
        self.kv.unset("db_pass")

    def save_pgsql_conf(self, db):
        """Configure GitLab with knowledge of a related PostgreSQL endpoint."""
        hookenv.log(db, hookenv.DEBUG)
        if db:
            hookenv.log(
                "Saving related PostgreSQL database config: {}".format(db.master),
                hookenv.DEBUG,
            )
            self.kv.set("pgsql_host", db.master.host)
            self.kv.set("pgsql_port", db.master.port)
            self.kv.set("pgsql_db", db.master.dbname)
            self.kv.set("pgsql_user", db.master.user)
            self.kv.set("pgsql_pass", db.master.password)

    def save_mysql_conf(self, db):
        """Configure GitLab with knowledge of a related PostgreSQL endpoint."""
        hookenv.log(db, hookenv.DEBUG)
        if db:
            hookenv.log(
                "Configuring related MySQL database: {}".format(db), hookenv.DEBUG
            )
            self.kv.set("mysql_host", db.host())
            self.kv.set("mysql_port", db.port())
            self.kv.set("mysql_db", db.database())
            self.kv.set("mysql_user", db.user())
            self.kv.set("mysql_pass", db.password())

    def save_redis_conf(self, endpoint):
        """Configure GitLab with knowledge of a related Redis instance."""
        try:
            redis = endpoint.relation_data()[0]
        except IndexError:
            return  # No relation data yet
        self.kv.set("redis_host", redis.get("host"))
        self.kv.set("redis_port", redis.get("port"))
        if redis.get("password"):
            self.kv.set("redis_pass", redis.get("password"))
        else:
            self.kv.unset("redis_pass")

    def remove_redis_conf(self):
        """Remove Redis configuation from the unit KV store."""
        self.kv.unset("redis_host")
        self.kv.unset("redis_port")
        self.kv.unset("redis_pass")

    def add_sources(self):
        """Ensure the GitLab apt repository is configured and updated for use."""
        distro = host.get_distrib_codename()
        apt_repo = self.charm_config.get("apt_repo")
        apt_key = self.charm_config.get("apt_key")
        apt_line = "deb {}/{}/ubuntu {} main".format(
            apt_repo, self.package_name, distro
        )
        hookenv.log(
            "Installing and updating apt source for {}: {} key {})".format(
                self.package_name, apt_line, apt_key
            )
        )
        add_source(apt_line, apt_key)

    def fetch_gitlab_apt_package(self):
        """Return reference to GitLab package information in the APT cache."""
        self.add_sources()
        apt_update()
        apt_cache = ubuntu_apt_pkg.Cache()
        hookenv.log("Fetching package information for {}".format(self.package_name))
        package = False
        try:
            package = apt_cache[self.package_name]
        except KeyError as ke:
            hookenv.log(
                "Fetching package information failed for {} with {}".format(
                    self.package_name, ke
                )
            )
            package = False
        return package

    def get_major_version(self, version):
        """Return the major version number of the provided version."""
        major = semantic_version.Version(version).major
        hookenv.log(
            "Found major version {} for GitLab version {}".format(major, version)
        )
        return major

    def get_latest_version(self, package):
        """Return the most recent version from a list of versions."""
        # return false if not installed
        latest_version = False
        if package.version:
            latest_version = package.version
        if latest_version:
            hookenv.log("Found latest GitLab version {}".format(latest_version))
        else:
            hookenv.log("GitLab package not found in index")
        return latest_version

    def get_installed_version(self, package):
        """Return the installed GitLab package version."""
        # return False if not installed
        installed_version = False
        if package and package.current_ver and "ver_str" in package["current_ver"]:
            installed_version = package.current_ver["ver_str"]
        if installed_version:
            hookenv.log(
                "Installed package version for GitLab is {}".format(installed_version)
            )
        else:
            hookenv.log("GitLab is not installed.")
        return installed_version

    def gitlab_reconfigure_run(self):
        """Run gitlab-ctl reconfigure."""
        subprocess.check_output(
            ["/usr/bin/gitlab-ctl", "reconfigure"], stderr=subprocess.STDOUT
        )

    def upgrade_package(self, version=None):
        """Upgrade GitLab to a specific version given an apt package version or wildcard."""
        if version:
            apt_install("{}={}".format(self.package_name, version), fatal=True)
        else:
            apt_install("{}".format(self.package_name), fatal=True)

    def upgrade_gitlab(self):
        """Check if a major version upgrade is being performed and install upgrades in the correct order."""
        hookenv.log("Processing pending package upgrades for GitLab")
        # loop until we're at the right version, stepping through major versions as needed
        # we'll also run reconfigure at each step of the upgrade, to make sure migrations are run
        major_upgrade = False
        while True:
            package = self.fetch_gitlab_apt_package()
            installed_version = self.get_installed_version(package)
            if package and installed_version:
                latest_version = self.get_latest_version(package)
                if self.charm_config["version"]:
                    desired_version = self.charm_config["version"]
                else:
                    desired_version = latest_version
                desired_major = self.get_major_version(desired_version)
                installed_major = self.get_major_version(installed_version)

                if desired_version == installed_version:
                    hookenv.log(
                        "GitLab is already at configured version {}".format(
                            desired_version
                        )
                    )
                    if major_upgrade:
                        # Return True if we have been through a major upgrade
                        # already
                        return True
                    return False
                elif desired_major == installed_major:
                    hookenv.log(
                        "Upgrading GitLab version {} to latest in major release {}".format(
                            installed_version, desired_major
                        )
                    )
                    self.upgrade_package("{}.*".format(desired_major))
                    self.gitlab_reconfigure_run()
                    return True
                else:
                    next_major = installed_major + 1
                    # first, make sure we're on the latest minor for this major
                    hookenv.log(
                        "Upgrading GitLab version {} to latest in current major release {}".format(
                            installed_version, installed_major
                        )
                    )
                    self.upgrade_package("{}.*".format(installed_major))
                    self.gitlab_reconfigure_run()
                    # then, upgrade to next major
                    hookenv.log(
                        "Upgrading GitLab version {} to latest in next major release {}".format(
                            installed_version, next_major
                        )
                    )
                    self.upgrade_package("{}.*".format(next_major))
                    self.gitlab_reconfigure_run()
                    major_upgrade = True
                    # we will loop here by default, to finish up the next upgrade steps
                    # looping means that we will only get into more complex checking in this
                    # branch of the logic if necessary, if we are already able to do a simple
                    # upgrade without all this hand-holding, we will go straight there when
                    # upgrade-gitlab is called. Wheeeeeee here we go!
            else:
                # not installed
                hookenv.log("GitLab is not installed, installing...")
                self.upgrade_package()
                return True

    def render_config(self):
        """Render the configuration for GitLab omnibus."""
        if self.pgsql_configured():
            templating.render(
                "gitlab.rb.j2",
                self.gitlab_config,
                {
                    "db_adapter": "postgresql",
                    "db_host": self.kv.get("pgsql_host"),
                    "db_port": self.kv.get("pgsql_port"),
                    "db_database": self.kv.get("pgsql_db"),
                    "db_user": self.kv.get("pgsql_user"),
                    "db_password": self.kv.get("pgsql_pass"),
                    "redis_host": self.kv.get("redis_host"),
                    "redis_port": self.kv.get("redis_port"),
                    "http_port": self.charm_config.get("http_port"),
                    "ssh_host": self.get_sshhost(),
                    "ssh_port": self.get_sshport(),
                    "smtp_enabled": self.get_smtp_enabled(),
                    "smtp_server": self.charm_config.get("smtp_server"),
                    "smtp_port": self.charm_config.get("smtp_port"),
                    "smtp_user": self.charm_config.get("smtp_user"),
                    "smtp_password": self.charm_config.get("smtp_password"),
                    "smtp_domain": self.get_smtp_domain(),
                    "smtp_authentication": self.charm_config.get("smtp_authentication"),
                    "smtp_tls": str(self.charm_config.get("smtp_tls")).lower(),
                    "email_from": self.charm_config.get("email_from"),
                    "email_display_name": self.charm_config.get("email_display_name"),
                    "email_reply_to": self.charm_config.get("email_reply_to"),
                    "url": self.get_external_uri(),
                },
            )
        elif self.mysql_configured():
            templating.render(
                "gitlab.rb.j2",
                self.gitlab_config,
                {
                    "db_adapter": "mysql2",
                    "db_host": self.kv.get("mysql_host"),
                    "db_port": self.kv.get("mysql_port"),
                    "db_database": self.kv.get("mysql_db"),
                    "db_user": self.kv.get("mysql_user"),
                    "db_password": self.kv.get("mysql_pass"),
                    "redis_host": self.kv.get("redis_host"),
                    "redis_port": self.kv.get("redis_port"),
                    "http_port": self.charm_config["http_port"],
                    "ssh_host": self.get_sshhost(),
                    "ssh_port": self.get_sshport(),
                    "smtp_enabled": self.get_smtp_enabled(),
                    "smtp_server": self.charm_config.get("smtp_server"),
                    "smtp_port": self.charm_config.get("smtp_port"),
                    "smtp_user": self.charm_config.get("smtp_user"),
                    "smtp_password": self.charm_config.get("smtp_password"),
                    "smtp_domain": self.get_smtp_domain(),
                    "smtp_authentication": self.charm_config.get("smtp_authentication"),
                    "smtp_tls": str(self.charm_config.get("smtp_tls")).lower(),
                    "email_from": self.charm_config.get("email_from"),
                    "email_display_name": self.charm_config.get("email_display_name"),
                    "email_reply_to": self.charm_config.get("email_reply_to"),
                    "url": self.get_external_uri(),
                },
            )
        elif self.legacy_db_configured():
            templating.render(
                "gitlab.rb.j2",
                self.gitlab_config,
                {
                    "db_adapter": "mysql2",
                    "db_host": self.kv.get("db_host"),
                    "db_port": self.kv.get("db_port"),
                    "db_database": self.kv.get("db_db"),
                    "db_user": self.kv.get("db_user"),
                    "db_password": self.kv.get("db_pass"),
                    "redis_host": self.kv.get("redis_host"),
                    "redis_port": self.kv.get("redis_port"),
                    "http_port": self.charm_config["http_port"],
                    "ssh_host": self.get_sshhost(),
                    "ssh_port": self.get_sshport(),
                    "smtp_enabled": self.get_smtp_enabled(),
                    "smtp_server": self.charm_config.get("smtp_server"),
                    "smtp_port": self.charm_config.get("smtp_port"),
                    "smtp_user": self.charm_config.get("smtp_user"),
                    "smtp_password": self.charm_config.get("smtp_password"),
                    "smtp_domain": self.get_smtp_domain(),
                    "smtp_authentication": self.charm_config.get("smtp_authentication"),
                    "smtp_tls": str(self.charm_config.get("smtp_tls")).lower(),
                    "email_from": self.charm_config.get("email_from"),
                    "email_display_name": self.charm_config.get("email_display_name"),
                    "email_reply_to": self.charm_config.get("email_reply_to"),
                    "url": self.get_external_uri(),
                },
            )
        else:
            hookenv.status_set(
                "blocked",
                "DB configuration is missing. Verify database relations to continue.",
            )
            hookenv.log("Skipping configuration due to missing DB config")
            return False
        if any_file_changed([self.gitlab_config]):
            self.gitlab_reconfigure_run()
        return True

    def open_ports(self):
        """Open ports based on configuration."""
        ports = ["80", str(self.charm_config["ssh_port"])]
        opened_ports = hookenv.opened_ports()
        for open_port in opened_ports:
            port_no = open_port.split("/")[0]
            if port_no not in ports:
                hookenv.close_port(port_no)
        for port in ports:
            if "{}/tcp".format(port) not in opened_ports:
                hookenv.open_port(port)

    def close_ports(self):
        """Close all open ports."""
        opened_ports = hookenv.opened_ports()
        for open_port in opened_ports:
            port_no = open_port.split("/")[0]
            hookenv.close_port(port_no)

    def configure(self):
        """
        Configure GitLab.

        Templates the configuration of the GitLab omnibus installer and
        runs the configuration routine to configure and start related services
        based on charm configuration and relation data.
        """
        if self.render_config():
            self.open_ports()
        else:
            self.close_ports()

        # check for upgrades
        self.upgrade_gitlab()

        return True

    def backup(self):
        """Run Gitlab backup and backup from layer-backup."""
        cmd = ["sudo", "gitlab-backup", "create", "STRATEGY=copy"]
        subprocess.check_output(cmd, stderr=subprocess.STDOUT)
        bh = BackupHelper()
        bh.backup()
