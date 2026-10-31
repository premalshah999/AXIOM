"""Where the application's database lives: on this computer (Local), on Supabase, or on a PostgreSQL server of your own.

`choice` is the setting and how it becomes connection URLs, `remote_db` is what the database layer does differently across a
network, `check` tests a connection before anything changes and `switch` moves the data. Local is the default and changes nothing.
"""
