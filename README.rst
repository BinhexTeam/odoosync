.. image:: https://img.shields.io/badge/licence-AGPL--3-blue.svg
    :alt: License: AGPL-3

odoosync
========

This Python package allows to sync between one Odoo and another.

Main features:

* Sync between different versions of Odoo
* Specify which models to sync
* Specify which records to sync, by specifying a domain
* Initial sync of all specified models/records
* Subsequent syncs only sync changed records
* Follow many2one relations to sync dependent records
* Manual mapping table (id - id) for records that cannot be synced
  (eg. for company ids, country records, analytic account ids...)
* Field-to-field mapping with automatic type conversion when possible
* Automatically reuse destination records that share XML IDs with source data
* Correctly sync recursive parent_id relations
* Exclude certain fields from sync
* Include only certain fields in sync
* Bidirectional sync
* 'Dry run' mode

Modern architecture
===================

The refactored codebase exposes a modular API that mirrors the main building
blocks of a sync session:

* ``odoosync.connection`` wraps connection handling through :class:`OdooInstance`
  and centralises credential discovery (including support for custom netrc
  paths and the ``ODOOSYNC_NETRC``/``NETRC`` environment variables).
* ``odoosync.models`` provides :class:`OdooModel` together with shared
  constants such as ``DEFAULT_EXCLUDED_FIELDS`` and
  ``INTERNAL_RUNTIME_FIELDS``.
* ``odoosync.sync`` contains :class:`ModelSyncer`, the high-level orchestrator
  that coordinates preparation and data transfer.
* ``odoosync.core`` offers cross-cutting helpers like
  :func:`get_logger`, :func:`set_level`, and :class:`SyncException`.

This layout keeps responsibilities focused while preserving backwards
compatibility with historical imports.

Installation
============

With pip
--------

To install or upgrade::

    pip install --user https://github.com/sunflowerit/odoosync/archive/master.zip

To add the script to the path, add the following to your `$HOME/.profile`::

    if [ -d "$HOME/.local/bin" ] ; then
        PATH="$HOME/.local/bin:$PATH"
    fi

Using the Anybox buildout recipe
--------------------------------

Add the following to `buildout.cfg`::

    [buildout]
    extensions = gp.vcsdevelop
    vcs-extend-develop = git+ssh://git@github.com/sunflowerit/odoosync.git@master#egg=odoosync-0.1

    [odoo]
    odoo_scripts =
        odoosync=odoosync

Configuration
=============

To specify login and password, put this in `$HOME/.netrc`::

    machine source.domain.tld login me@sunflowerweb.nl password mypassword
    machine destination.domain.tld login me@sunflowerweb.nl password mypassword

If your credentials live in another location, set a custom path either in the YAML file::

    source:
      host: source.domain.tld
      netrc_path: /etc/odoo/source.netrc

    target:
      host: destination.domain.tld
      netrc_path: /etc/odoo/destination.netrc

or globally for all endpoints::

    options:
      netrc_path: /etc/odoo/shared.netrc

The path accepts ``~`` expansion. The environment variables ``ODOOSYNC_NETRC`` and ``NETRC`` are also honoured as fallbacks.

To specify which models to sync, create a YAML file.
Please see the `YAML examples <https://github.com/sunflowerit/odoosync/blob/master/examples>`_.

Usage
=====

From command line::

    odoosync mysyncfile.yaml

Provide a one-off credentials file with::

  odoosync mysyncfile.yaml --netrc-file /etc/odoo/credentials.netrc

Include dependent records discovered through many2one relations by either
passing ``--sync-dependencies`` on the CLI or setting
``options.sync_dependencies`` to ``true`` in the YAML file::

  odoosync mysyncfile.yaml --netrc-file /etc/odoo/credentials.netrc --sync-dependencies

Automatic reuse of records by XML ID is enabled by default. Set
``options.auto_xmlid_lookup`` to ``false`` in the YAML file if you prefer to
force manual mappings for module-provided data instead of relying on shared
external identifiers.

To remap fields between source and destination models, add a ``field_mapping``
section inside the model definition in your YAML file::

  - model: res.partner
    field_mapping:
      x_field: y_field

odoosync copies values from ``x_field`` into ``y_field`` and attempts safe
conversions (booleans to integers, integers to floats, many2one relations to
text, and more). When a conversion is not supported, the mapping is skipped and
a warning is logged so the record continues untouched.

Programmatic usage
------------------

Import the new modular API when embedding odoosync in your own tools:

.. code-block:: python

    from odoosync.connection import OdooInstance
    from odoosync.core import SyncException, get_logger, set_level
    from odoosync.models import OdooModel
    from odoosync.sync import ModelSyncer

Legacy applications can continue importing ``odoosync.ModelSyncer``; the legacy
module now re-exports the classes above and raises a ``DeprecationWarning`` to
help plan migrations.

Known issues / Roadmap
======================

* Initial sync not tested for bidirectional configuration. Probably it will go well, otherwise sync one way first and later add the reverse sync rules.
* Also sync deletions (or make the record inactive)
* Allow defining python functions in the YAML in order to do conversion of field values
* Allow defining model mapping in the YAML in order to sync from one model to another model (eg. issues to tasks)
* In order to speed up initial sync, use Odoo's import_data or load functionality over RPC to bulk create records, instead of importing one by one
* Investigate the use of Odoo's import functionality to resolve many2one relationships by intelligent naming of xmlids
* Should we refactor and store external identifiers on both sides? Will make the code cleaner and probably faster
* Make a Javascript/PouchDB version, so that it also can be used in web applications (eg. a cool ETL tool)
* Save loaded records and translation tables in a lock file (or in PouchDB), so that we dont have to reload from server on next sync. This could also be a case for making the syncer into a permanently running daemon
* YAML structure could be prettier, eg.::
      models:
        * normal:
          res.partner:
          - ...
        * dependent: [....]
      reverse_models
        * normal:
          res.partner:
          - ...
        * dependent: [....]
* Correctly solve sync conflicts by carefully looking at the timestamp, taking into account the clock difference between the two odoo instances. (current behaviour: normal sync 'wins' because it is first
* When syncing mails, use a context of mail_create_nosubscribe = True

Credits
=======

Contributors
------------

* Hayyan Ebrahem
* Tom Blauwendraat

Maintainer
----------

This module is maintained by Sunflower IT.

