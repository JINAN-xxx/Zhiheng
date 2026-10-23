create table if not exists agent_skill (
  skill_id       varchar(36)  not null,
  skill_slug     varchar(100) not null,
  skill_name     varchar(100) not null,
  description    varchar(500) not null default '',
  version        varchar(64)  not null default '1.0.0',
  status         varchar(16)  not null default 'enabled',
  source_type    varchar(16)  not null default 'upload',
  storage_path   varchar(512) not null,
  content_hash   varchar(64)  not null,
  metadata_json  jsonb        not null default '{}'::jsonb,
  lock_version   integer      not null default 1,
  created_by     varchar(64)  not null default '',
  created_at     timestamp(0) not null,
  updated_by     varchar(64)  not null default '',
  updated_at     timestamp(0) not null,
  primary key (skill_id),
  unique (skill_slug),
  constraint ck_agent_skill_status check (status in ('enabled', 'disabled')),
  constraint ck_agent_skill_source_type check (source_type in ('builtin', 'upload')),
  constraint ck_agent_skill_lock_version check (lock_version > 0)
);

comment on table agent_skill is '平台共享技能表';
create index if not exists idx_agent_skill_status on agent_skill (status);

