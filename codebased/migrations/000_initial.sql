create table file
(
    -- Note: this is relative to the repository root.
    path             text primary key,
    size_bytes       integer,
    -- st_mtime_ns
    last_modified_ns integer,
    -- sha256 digest bytes
    sha256_digest    blob
);

create table object
(
    id             integer primary key,
    path           text,
    name           text,
    language       text,
    context_before text,
    context_after  text,
    kind           text,
    byte_range     text,
    coordinates    text,
    foreign key (path) references file (path)
);

create table embedding
(
    object_id       integer primary key,
    embedding       blob,
    content_sha_256 blob,
    foreign key (object_id) references object (id)
);

create index embedding_content_sha_256_index on embedding (content_sha_256);