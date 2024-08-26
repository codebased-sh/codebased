create table if not exists file_revision
(
    id            integer primary key,
    path          text,
    size          integer,
    last_modified float,
    hash          text,
    unique (path, hash)
);

create table if not exists object
(
    id             integer primary key,
    file_revision  integer foreign key references file_revision (id),
    name           text,
    language       text,
    context_before text,
    context_after  text,
    kind           text,
    byte_range     text,
    coordinates    text
);

create table if not exists embedding
(
    id           integer primary key,
    object_id    integer foreign key references object (id),
    /* Struct packed F32 */
    embedding    blob,
    content_hash text
);