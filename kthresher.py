#!/usr/bin/env python
from __future__ import print_function


# Copyright 2015 Tony Garcia
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tool to Purge Unused Kernels
Removes those kernel packages marked as candidate for autoremoval.
Those packages are generally installed via Unattended upgrade or meta-packages
By default the latest kernel and manual installations are marked to Never
Auto Remove.

Ubuntu has multiple suggestions on how to remove kernels:
  https://help.ubuntu.com/community/RemoveOldKernels

A good recommendation is using unattendend-upgrades as it has an option to
remove unused dependencies, but that applies to all packages not just kernels.

thresher - A device that first separates the head of a stalk of grain from
the straw, and then further separates the kernel from the rest of the head.
"""

import re
import sys
import logging
import argparse
from glob import iglob
from platform import uname
from distutils.version import LooseVersion

try:
    import configparser
except ImportError:
    import ConfigParser as configparser

try:
    Parser = configparser.ConfigParser
except AttributeError:
    Parser = configparser.SafeConfigParser

try:
    import apt
except ImportError:
    try:
        import distro
    except ImportError:
        from platform import dist

        DISTRO = dist()[0]
    else:
        DISTRO = distro.linux_distribution(False)[0]

    if DISTRO == "debian" or DISTRO == "Ubuntu":
        print(
            "Error: python apt library was not found\n"
            "python-apt and/or python3-apt packages provide it.",
            file=sys.stderr,
        )
    else:
        print("Error: {0} distro not supported".format(DISTRO), file=sys.stderr)
    sys.exit(1)


__version__ = "1.3.1"


def cmp_to_key(mycmp):
    """Convert a cmp= function into a key= function
    Wrapper to leverage the use of apt_pkg.version_compare.
    As documented in:
    https://docs.python.org/3/howto/sorting.html#the-old-way-using-the-cmp-parameter
    """

    class K:
        def __init__(self, obj, *args):
            self.obj = obj

        def __lt__(self, other):
            return mycmp(self.obj, other.obj) < 0

        def __gt__(self, other):
            return mycmp(self.obj, other.obj) > 0

        def __eq__(self, other):
            return mycmp(self.obj, other.obj) == 0

        def __le__(self, other):
            return mycmp(self.obj, other.obj) <= 0

        def __ge__(self, other):
            return mycmp(self.obj, other.obj) >= 0

        def __ne__(self, other):
            return mycmp(self.obj, other.obj) != 0

    return K


def get_configs(conf_file, section):
    """Obtains the configs from a file.
    Config file format: INI
    Valid sections: main
    Valid options: headers, include, keep, purge, verbose
    Example:
    [main]
    headers=(yes|on|true|no|off|false)
    include=/path/to/dir/
    keep=[0-9]
    purge=(yes|on|true|no|off|false)
    verbose=(yes|on|true|no|off|false)
    """
    valid_configs = {
        "headers": "boolean",
        "include": "str",
        "keep": "int",
        "purge": "boolean",
        "verbose": "boolean",
    }
    configs = {}
    def_conf = Parser()
    logging.info("Attempting to read {0}.".format(conf_file))
    try:
        def_conf.read(conf_file)
    except configparser.ParsingError:
        logging.error("Error, File contains parsing errors: {0}".format(conf_file))
        sys.exit(1)
    if not def_conf.read(conf_file):
        logging.info(
            "Config file {0} is empty or does not exist, ignoring.".format(conf_file)
        )
        return configs
    if not def_conf.has_section(section):
        logging.info("Unable to find section [{0}].".format(section))
        return configs
    if len(def_conf.options(section)) < 1:
        logging.info("No options found in section [{0}].".format(section))
        return configs
    logging.info("Options found: {0}.".format(def_conf.options(section)))
    # Validation of the options found
    for option in def_conf.options(section):
        if option not in valid_configs.keys():
            logging.info('Invalid setting "{0}", ignoring'.format(option))
        else:
            logging.info('Valid setting found "{0}"'.format(option))
            if valid_configs[option] == "int":
                try:
                    configs[option] = def_conf.getint(section, option)
                except configparser.NoOptionError:
                    logging.error(
                        'Error, unable to get value from "{0}".'.format(option)
                    )
                    sys.exit(1)
                if option == "keep":
                    if configs[option] > 9:
                        logging.error('Error, "keep" should be between 0-9.')
                        sys.exit(1)
            elif valid_configs[option] == "boolean":
                try:
                    configs[option] = def_conf.getboolean(section, option)
                except configparser.NoOptionError:
                    logging.error(
                        'Error, unable to get value from "{0}".'.format(option)
                    )
                    sys.exit(1)
            elif valid_configs[option] == "str":
                try:
                    configs[option] = def_conf.get(section, option)
                except configparser.NoOptionError:
                    logging.error(
                        'Error, unable to get value from "{0}".'.format(option)
                    )
            logging.info("\t{0} = {1}".format(option, configs[option]))
    if "include" in configs.keys():
        # Obtain the configs on each nested config file.
        for nested_file in sorted(iglob(configs["include"])):
            # Aborting if importing the same config file.
            # Won't prevent indirect loops.
            if nested_file == conf_file:
                logging.error("Error, looping config files, aborting...")
                sys.exit(1)
            nested_configs = get_configs(nested_file, section)
            # Override any option coming from the nested configs.
            for nested_config in nested_configs:
                configs[nested_config] = nested_configs[nested_config]
    return configs


def show_autoremovable_pkgs():
    """List all the kernel related packages available for autoremoval.
    """
    packages = {}
    ver_max_len = 0
    try:
        apt_cache = apt.Cache()
    except SystemError:
        logging.error("Unable to obtain the cache!")
        sys.exit(1)
    for pkg_name in apt_cache.keys():
        pkg = apt_cache[pkg_name]
        if (pkg.is_installed and pkg.is_auto_removable) and re.match(
            "^linux-(image|(\w+-)?headers)-.*$", pkg_name
        ):
            packages[pkg_name] = pkg.installed.version
            if ver_max_len < len(pkg.installed.version):
                ver_max_len = len(pkg.installed.version)
    if packages:
        logging.info("List of kernel packages available for autoremoval:")
        logging.info(
            "{0:>{width}} {1:<{width}}".format(
                "Version", "Package", width=ver_max_len + 2
            )
        )
        for package in sorted(packages.keys()):
            logging.info(
                "{0:>{width}} {1:<{width}}".format(
                    packages[package], package, width=ver_max_len + 2
                )
            )
    else:
        logging.info("No kernel packages available for autoremoval.")


def kthreshing(purge=None, headers=None, keep=1):
    """Purge or list the unused kernels.
    By default keeps 1.
    The running kernel, the kernels marked as NeverAutoRemove and
    Manually installed kernels are nevertouched by kthresher.
    """
    kernels = {}
    ver_max_len = 0
    kernel_image_regex = "^linux-image-.*$"
    kernel_header_regex = "^linux-(\w+-)?headers-.*$"
    try:
        apt_cache = apt.Cache()
    except SystemError:
        logging.error("Unable to obtain the cache!")
        sys.exit(1)
    current_kernel_ver = uname()[2]
    kernel_pkg = apt_cache["linux-image-%s" % current_kernel_ver]
    logging.info(
        "Running kernel is {0} v[{1}]".format(
            kernel_pkg.name, kernel_pkg.installed.version
        )
    )
    for pkg_name in apt_cache.keys():
        pkg = apt_cache[pkg_name]
        if (pkg.is_installed and pkg.is_auto_removable) and (
            "kernel" in pkg.section and re.match(kernel_image_regex, pkg_name)
        ):
            if ver_max_len < len(pkg.installed.version):
                ver_max_len = len(pkg.installed.version)
            kernels.setdefault(pkg.installed.version, []).append(pkg.name)
    if headers:
        for pkg_name in apt_cache.keys():
            pkg = apt_cache[pkg_name]
            if (pkg.is_installed and pkg.is_auto_removable) and re.match(
                kernel_header_regex, pkg_name
            ):
                if pkg.installed.version in kernels.keys():
                    kernels[pkg.installed.version].append(pkg.name)
                else:
                    kernels[pkg.installed.version] = [pkg.name]
    if kernels:
        logging.info("Attempting to keep {0} kernel package(s)".format(keep))
        kernel_versions = list(kernels.copy().keys())
        logging.info(
            "Found {0} kernel image(s) installed and available for "
            "autoremoval".format(len(kernel_versions))
        )
        logging.info("Pre-sorting: {0}".format(kernel_versions))
        try:
            # Sadly this is broken in python3, https://bugs.python.org/issue14894
            sorted_kernel_list = sorted(kernel_versions, key=LooseVersion)
        except TypeError:
            # Using apt_pkg.version_compare
            # https://github.com/rackerlabs/kthresher/pull/61
            sorted_kernel_list = sorted(
                kernel_versions, key=cmp_to_key(apt.apt_pkg.version_compare)
            )

        logging.info("Post-sorting: {0}".format(sorted_kernel_list))
        if keep >= len(kernel_versions):
            logging.error(
                "Nothing to do, attempting to keep {0} out of {1} "
                "kernel images.".format(keep, len(kernel_versions))
            )
            sys.exit(1)
        else:
            for index in range(0, len(sorted_kernel_list) - keep):
                kernel_version = sorted_kernel_list[index]
                logging.info(
                    "\tPurging packages from version: {0}".format(kernel_version)
                )
                for pkg_name in kernels[kernel_version]:
                    logging.info("\t\tPurging: {0}".format(pkg_name))
                    if purge:
                        pkg = apt_cache[pkg_name]
                        pkg.mark_delete(purge=True)
            if purge:
                try:
                    apt_cache.commit(
                        fetch_progress=apt.progress.text.AcquireProgress(),
                        install_progress=apt.progress.base.InstallProgress(),
                    )
                except apt.cache.LockFailedException as lfe:
                    logging.error("{}, are you root?".format(lfe))
                    sys.exit(1)
                except SystemError:
                    logging.error("Unable to commit the changes")
                    sys.exit(1)
    else:
        logging.info("No packages available for autoremoval.")


def main():
    """The main function.
    """
    defaults = {
        "config": {"file": "/etc/kthresher.conf", "section": "main"},
        "options": {
            "dry_run": False,
            "headers": False,
            "keep": 1,
            "purge": False,
            "verbose": False,
        },
    }
    options = defaults["options"].copy()
    conf_options = {}
    parser = argparse.ArgumentParser(
        description="Purge Unused Kernels.", prog="kthresher"
    )
    parser.add_argument(
        "-c",
        "--config",
        type=str,
        metavar="FILE",
        help="Config file, default is /etc/kthresher.conf",
    )
    parser.add_argument(
        "-d",
        "--dry-run",
        action="store_true",
        help="List unused kernel images available to purge"
        "(dry run). Is always verbose.",
    )
    parser.add_argument(
        "-H",
        "--headers",
        action="store_true",
        help="Include the search for kernel headers.",
    )
    parser.add_argument(
        "-k",
        "--keep",
        nargs="?",
        type=int,
        const=0,
        metavar="N",
        choices=range(0, 10),
        help="Number of kernels to keep, default 1.",
    )
    parser.add_argument(
        "-p", "--purge", help="Purge Unused Kernels.", action="store_true"
    )
    parser.add_argument(
        "-s",
        "--show-autoremoval",
        action="store_true",
        help="Show kernel packages available for autoremoval.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Be verbose.")
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version="%(prog)s v{0}".format(__version__),
        help="Print version.",
    )
    args = parser.parse_args()

    # Logging
    if args.verbose or args.dry_run:
        log_level = logging.INFO
    else:
        log_level = logging.ERROR
    logging.basicConfig(format="%(levelname)s: %(message)s", level=log_level)
    # Read config files
    if args.config:
        conf_options = get_configs(args.config, defaults["config"]["section"])
    else:
        conf_options = get_configs(
            defaults["config"]["file"], defaults["config"]["section"]
        )
    # Overriding options as follows:
    # defaults -> default config file or custom config file -> included config
    # -> cli arguments
    # First overriding default configs from a file if available:
    if conf_options:
        options.update(conf_options)
    # Override the verbosity if set through configuration
    if options["verbose"]:
        logging.getLogger().setLevel(logging.INFO)
    # Now overriding the result options with cli arguments
    if args.dry_run:
        options["dry_run"] = args.dry_run
    if args.headers:
        options["headers"] = args.headers
    if args.keep is not None:
        options["keep"] = args.keep
    if args.purge:
        options["purge"] = args.purge
    if args.verbose:
        options["verbose"] = args.verbose
    logging.info("Options: {0}".format(options))
    # Show auto-removable, this is only available via explicit argument
    if args.show_autoremoval:
        logging.getLogger().setLevel(logging.INFO)
        show_autoremovable_pkgs()
        sys.exit(0)
    if options["dry_run"]:
        logging.info("----- DRY RUN -----")
        kthreshing(purge=False, headers=options["headers"], keep=options["keep"])
        sys.exit(0)
    if options["purge"]:
        kthreshing(purge=True, headers=options["headers"], keep=options["keep"])
        sys.exit(0)
    if not sys.stdout.isatty():
        sys.exit(0)
    else:
        logging.error("No argument used.")
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
