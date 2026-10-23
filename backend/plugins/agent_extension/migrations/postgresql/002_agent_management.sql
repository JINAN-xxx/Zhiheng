create table if not exists agent_configuration (
  agent_id          varchar(36)  primary key,
  owner_user_id     bigint       not null,
  name              varchar(100) not null,
  description       varchar(500) not null default '',
  model_id          varchar(36)  not null,
  system_prompt     text         not null,
  skill_slugs       jsonb        not null,
  knowledge_base_ids jsonb       not null,
  application_id    varchar(36)  null,
  temperature       double precision not null default 0.7,
  max_tokens        integer      not null default 2048,
  status            varchar(16)  not null default 'draft',
  version           integer      not null default 1,
  published_config  jsonb        null,
  published_version integer      null,
  published_at      timestamp    null,
  created_at        timestamp    not null,
  updated_at        timestamp    not null,
  constraint ck_agent_configuration_status check (status in ('draft', 'published')),
  constraint ck_agent_configuration_version check (version > 0),
  constraint ck_agent_configuration_published_version check (published_version is null or published_version > 0),
  constraint ck_agent_configuration_temperature check (temperature >= 0 and temperature <= 2),
  constraint ck_agent_configuration_max_tokens check (max_tokens > 0)
);

create index if not exists idx_agent_configuration_owner_status on agent_configuration (owner_user_id, status);
