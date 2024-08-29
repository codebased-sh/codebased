create table repository
(
    id   integer primary key,
    path text,
    type text,
    unique (path)
);

create table file_revision
(
    id            integer primary key,
    repository_id integer,
    path          text,
    size          integer,
    last_modified float,
    hash          text,
    foreign key (repository_id) references repository (id),
    unique (repository_id, path, hash)
);

create table object
(
    id               integer primary key,
    file_revision_id integer,
    name             text,
    language         text,
    context_before   text,
    context_after    text,
    kind             text,
    byte_range       text,
    coordinates      text,
    foreign key (file_revision_id) references file_revision (id)
);

create table embedding
(
    object_id           integer primary key,
    /* Struct packed F32 */
    embedding    blob,
    content_hash text,
    foreign key (object_id) references object (id)
);