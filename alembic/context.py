from alembic import util
from sqlalchemy import MetaData, Table, Column, String, literal_column, \
    text
from sqlalchemy import create_engine
from sqlalchemy.engine import url as sqla_url
import sys
from alembic import ddl

import logging
log = logging.getLogger(__name__)

_meta = MetaData()
_version = Table('alembic_version', _meta, 
                Column('version_num', String(32), nullable=False)
            )

class Context(object):
    """Maintains state throughout the migration running process.
    
    Mediates the relationship between an ``env.py`` environment script, 
    a :class:`.ScriptDirectory` instance, and a :class:`.DefaultImpl` instance.

    The :class:`.Context` is available directly via the :func:`.get_context` function,
    though usually it is referenced behind the scenes by the various module level functions
    within the :mod:`alembic.context` module.
    
    """
    def __init__(self, dialect, script, connection, fn, 
                        as_sql=False, 
                        output_buffer=None,
                        transactional_ddl=None,
                        starting_rev=None):
        self.dialect = dialect
        self.script = script
        if as_sql:
            self.connection = self._stdout_connection(connection)
            assert self.connection is not None
        else:
            self.connection = connection
        self._migrations_fn = fn
        self.as_sql = as_sql
        self.output_buffer = output_buffer if output_buffer else sys.stdout

        self._start_from_rev = starting_rev
        self.impl = ddl.DefaultImpl.get_by_dialect(dialect)(
                            dialect, connection, self.as_sql,
                            transactional_ddl,
                            self.output_buffer
                            )

    def _current_rev(self):
        if self.as_sql:
            return self._start_from_rev
        else:
            if self._start_from_rev:
                raise util.CommandError(
                    "Can't specify current_rev to context "
                    "when using a database connection")
            _version.create(self.connection, checkfirst=True)
        return self.connection.scalar(_version.select())

    def _update_current_rev(self, old, new):
        if old == new:
            return
        if new is None:
            self.impl._exec(_version.delete())
        elif old is None:
            self.impl._exec(_version.insert().
                        values(version_num=literal_column("'%s'" % new))
                    )
        else:
            self.impl._exec(_version.update().
                        values(version_num=literal_column("'%s'" % new))
                    )

    def run_migrations(self, **kw):
        log.info("Context impl %s.", self.impl.__class__.__name__)
        if self.as_sql:
            log.info("Generating static SQL")
        log.info("Will assume %s DDL.", 
                        "transactional" if self.impl.transactional_ddl 
                        else "non-transactional")

        if self.as_sql and self.impl.transactional_ddl:
            self.impl.static_output("BEGIN;")

        current_rev = rev = False
        for change, prev_rev, rev in self._migrations_fn(
                                        self._current_rev()):
            if current_rev is False:
                current_rev = prev_rev
                if self.as_sql and not current_rev:
                    _version.create(self.connection)
            log.info("Running %s %s -> %s", change.__name__, prev_rev, rev)
            if self.as_sql:
                self.impl.static_output("-- Running %s %s -> %s" %(change.__name__, prev_rev, rev))
            change(**kw)
            if not self.impl.transactional_ddl:
                self._update_current_rev(prev_rev, rev)
            prev_rev = rev

        if rev is not False:
            if self.impl.transactional_ddl:
                self._update_current_rev(current_rev, rev)

            if self.as_sql and not rev:
                _version.drop(self.connection)

        if self.as_sql and self.impl.transactional_ddl:
            self.impl.static_output("COMMIT;")

    def execute(self, sql):
        self.impl._exec(sql)

    def _stdout_connection(self, connection):
        def dump(construct, *multiparams, **params):
            self.impl._exec(construct)

        return create_engine("%s://" % self.dialect.name, 
                        strategy="mock", executor=dump)

    @property
    def bind(self):
        """Return the current "bind".
        
        In online mode, this is an instance of
        :class:`sqlalchemy.engine.base.Connection`, and is suitable
        for ad-hoc execution of any kind of usage described 
        in :ref:`sqlexpression_toplevel` as well as 
        for usage with the :meth:`sqlalchemy.schema.Table.create`
        and :meth:`sqlalchemy.schema.MetaData.create_all` methods
        of :class:`.Table`, :class:`.MetaData`.
        
        Note that when "standard output" mode is enabled, 
        this bind will be a "mock" connection handler that cannot
        return results and is only appropriate for a very limited
        subset of commands.
        
        """
        return self.connection

config = None
"""The current :class:`.Config` object.

This is the gateway to the ``alembic.ini`` or other
.ini file in use for the current command.

This function does not require that the :class:`.Context` 
has been configured.

"""

_context_opts = {}
_context = None
_script = None

def _opts(cfg, script, **kw):
    """Set up options that will be used by the :func:`.configure_connection`
    function.
    
    This basically sets some global variables.
    
    """
    global config, _script
    _context_opts.update(kw)
    _script = script
    config = cfg

def _clear():
    global _context_opts, _context, _script
    _context = _script = None
    _context_opts = {}

def requires_connection():
    """Return True if the current migrations environment should have
    an active database connection.
    
    Currently, this is ``True`` or ``False`` depending 
    on the the ``--sql`` flag passed.

    This function does not require that the :class:`.Context` 
    has been configured.
    
    """
    return not _context_opts.get('as_sql', False)

def get_head_revision():
    """Return the hex identifier of the 'head' revision.

    This function does not require that the :class:`.Context` 
    has been configured.
    
    """
    return _script._as_rev_number("head")

def get_starting_revision_argument():
    """Return the 'starting revision' argument,
    if the revision was passed using ``start:end``.
    
    This is only meaningful in "offline" mode.
    Returns ``None`` if no value is available
    or was configured.

    This function does not require that the :class:`.Context` 
    has been configured.

    """
    if _context is not None:
        return _script._as_rev_number(get_context()._start_from_rev)
    elif 'starting_rev' in _context_opts:
        return _script._as_rev_number(_context_opts['starting_rev'])
    else:
        raise util.CommandError("No starting revision argument is available.")

def get_revision_argument():
    """Get the 'destination' revision argument.

    This is typically the argument passed to the 
    ``upgrade`` or ``downgrade`` command.
    
    If it was specified as ``head``, the actual 
    version number is returned; if specified
    as ``base``, ``None`` is returned.

    This function does not require that the :class:`.Context` 
    has been configured.

    """
    return _script._as_rev_number(_context_opts['destination_rev'])

def get_tag_argument():
    """Return the value passed for the ``--tag`` argument, if any.

    The ``--tag`` argument is not used directly by Alembic,
    but is available for custom ``env.py`` configurations that 
    wish to use it; particularly for offline generation scripts
    that wish to generate tagged filenames.

    This function does not require that the :class:`.Context` 
    has been configured.
    
    """
    return _context_opts.get('tag', None)

def configure(
        connection=None,
        url=None,
        dialect_name=None,
        transactional_ddl=None,
        output_buffer=None,
        starting_rev=None,
        tag=None
    ):
    """Configure the migration environment.
    
    The important thing needed here is first a way to figure out
    what kind of "dialect" is in use.   The second is to pass
    an actual database connection, if one is required.
    
    If the :func:`.requires_connection` function returns False,
    then no connection is needed here.  Otherwise, the
    ``connection`` parameter should be present as an 
    instance of :class:`sqlalchemy.engine.base.Connection`.
    
    This function is typically called from the ``env.py``
    script within a migration environment.  It can be called
    multiple times for an invocation.  The most recent :class:`~sqlalchemy.engine.base.Connection`
    for which it was called is the one that will be operated upon
    by the next call to :func:`.run_migrations`.
    
    :param connection: a :class:`sqlalchemy.engine.base.Connection`.  The type of dialect
     to be used will be derived from this.
    :param url: a string database url, or a :class:`sqlalchemy.engine.url.URL` object.
     The type of dialect to be used will be derived from this if ``connection`` is
     not passed.
    :param dialect_name: string name of a dialect, such as "postgresql", "mssql", etc.
     The type of dialect to be used will be derived from this if ``connection``
     and ``url`` are not passed.
    :param transactional_ddl: Force the usage of "transactional" DDL on or off;
     this otherwise defaults to whether or not the dialect in use supports it.
    :param output_buffer: a file-like object that will be used for textual output
     when the ``--sql`` option is used to generate SQL scripts.  Defaults to
     ``sys.stdout`` if not passed here.
    :param starting_rev: Override the "starting revision" argument when using
     ``--sql`` mode.
    :param tag: a string tag for usage by custom ``env.py`` scripts.  Set via
     the ``--tag`` option, can be overridden here.
     
    """

    if connection:
        dialect = connection.dialect
    elif url:
        url = sqla_url.make_url(url)
        dialect = url.get_dialect()()
    elif dialect_name:
        url = sqla_url.make_url("%s://" % dialect_name)
        dialect = url.get_dialect()()
    else:
        raise Exception("Connection, url, or dialect_name is required.")

    global _context
    opts = _context_opts
    if transactional_ddl is not None:
        opts["transactional_ddl"] =  transactional_ddl
    if output_buffer is not None:
        opts["output_buffer"] = output_buffer
    if starting_rev:
        opts['starting_rev'] = starting_rev
    if tag:
        opts['tag'] = tag
    _context = Context(
                        dialect, _script, connection, 
                        opts['fn'],
                        as_sql=opts.get('as_sql', False), 
                        output_buffer=opts.get("output_buffer"),
                        transactional_ddl=opts.get("transactional_ddl"),
                        starting_rev=opts.get("starting_rev")
                    )

def configure_connection(connection):
    """Deprecated; use :func:`alembic.context.configure`."""
    configure(connection=connection)

def run_migrations(**kw):
    """Run migrations as determined by the current command line configuration
    as well as versioning information present (or not) in the current 
    database connection (if one is present).

    The function accepts optional ``**kw`` arguments.   If these are
    passed, they are sent directly to the ``upgrade()`` and ``downgrade()``
    functions within each target revision file.   By modifying the
    ``script.py.mako`` file so that the ``upgrade()`` and ``downgrade()``
    functions accept arguments, parameters can be passed here so that
    contextual information, usually information to identify a particular
    database in use, can be passed from a custom ``env.py`` script
    to the migration functions.

    This function requires that a :class:`.Context` has first been 
    made available via :func:`.configure`.
    
    """
    get_context().run_migrations(**kw)

def execute(sql):
    """Execute the given SQL using the current change context.
    
    In a SQL script context, the statement is emitted directly to the 
    output stream.   There is *no* return result, however, as this
    function is oriented towards generating a change script
    that can run in "offline" mode.  For full interaction
    with a connected database, use the "bind" available 
    from the context::
    
        connection = get_context().bind
    
    Also note that any parameterized statement here *will not work*
    in offline mode - any kind of UPDATE or DELETE needs to render
    inline expressions.   Due to these limitations, 
    :func:`.execute` is overall not spectacularly useful for migration 
    scripts that wish to run in offline mode.  Consider using the Alembic 
    directives, or if the environment is only meant to run in 
    "online" mode, use the ``get_context().bind``.
    
    :param sql: Any legal SQLAlchemy expression, including:
    
    * a string
    * a :func:`sqlalchemy.sql.expression.text` construct, with the caveat that
      bound parameters won't work correctly in offline mode.
    * a :func:`sqlalchemy.sql.expression.insert` construct.  If working 
      in offline mode, consider using :func:`alembic.op.bulk_insert`
      instead to support parameterization.
    * a :func:`sqlalchemy.sql.expression.update`, :func:`sqlalchemy.sql.expression.insert`, 
      or :func:`sqlalchemy.sql.expression.delete`  construct, with the caveat
      that bound parameters won't work correctly in offline mode.
    * Pretty much anything that's "executable" as described
      in :ref:`sqlexpression_toplevel`.

    This function requires that a :class:`.Context` has first been 
    made available via :func:`.configure`.
    
    """
    get_context().execute(sql)

def get_context():
    """Return the current :class:`.Context` object.
    
    If :func:`.configure` has not been called yet, raises
    an exception.
    
    Generally, env.py scripts should access the module-level functions
    in :mod:`alebmic.context` to get at this object's functionality.
    
    """
    if _context is None:
        raise Exception("No context has been configured yet.")
    return _context

def get_impl():
    return get_context().impl