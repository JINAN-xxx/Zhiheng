create table if not exists agent_work_task (
  task_id                  varchar(36)  not null primary key,
  owner_user_id            bigint       not null,
  create_client_message_id varchar(100) not null,
  title                    varchar(100) not null,
  goal                     text         not null,
  status                   varchar(16)  not null default 'active',
  active_agent_id          varchar(36)  null,
  version                  integer      not null default 1,
  created_at               timestamp    not null,
  updated_at               timestamp    not null,
  constraint uk_agent_work_task_create_client unique (owner_user_id, create_client_message_id),
  constraint ck_agent_work_task_status check (status in ('active', 'completed', 'archived')),
  constraint ck_agent_work_task_version check (version > 0)
);

create index if not exists idx_agent_work_task_owner_status_updated
  on agent_work_task (owner_user_id, status, updated_at);

create table if not exists agent_work_task_member (
  task_id             varchar(36)  not null,
  agent_id            varchar(36)  not null,
  agent_name_snapshot varchar(100) not null,
  published_version   integer      not null,
  config_snapshot     jsonb        not null,
  added_at            timestamp    not null,
  removed_at          timestamp    null,
  primary key (task_id, agent_id),
  constraint fk_agent_work_task_member_task foreign key (task_id)
    references agent_work_task (task_id) on delete restrict,
  constraint ck_agent_work_task_member_published_version check (published_version > 0)
);

create index if not exists idx_agent_work_task_member_task_removed
  on agent_work_task_member (task_id, removed_at);

create table if not exists agent_work_message (
  message_id          varchar(36)  not null primary key,
  task_id             varchar(36)  not null,
  client_message_id   varchar(100) null,
  reply_to_message_id varchar(36)  null,
  role                varchar(16)  not null,
  content             text         not null,
  reasoning_content   text         not null default '',
  agent_id            varchar(36)  null,
  agent_name_snapshot varchar(100) null,
  published_version   integer      null,
  run_id              varchar(36)  null,
  run_status          varchar(16)  null,
  usage_snapshot      jsonb        null,
  error_snapshot      jsonb        null,
  created_at          timestamp    not null,
  constraint uk_agent_work_message_client unique (task_id, client_message_id),
  constraint uk_agent_work_message_run unique (run_id),
  constraint fk_agent_work_message_task foreign key (task_id)
    references agent_work_task (task_id) on delete restrict,
  constraint ck_agent_work_message_role check (role in ('user', 'assistant')),
  constraint ck_agent_work_message_run_status check (
    run_status is null or run_status in ('pending', 'streaming', 'completed', 'cancelled', 'interrupted', 'failed')
  ),
  constraint ck_agent_work_message_published_version check (
    published_version is null or published_version > 0
  ),
  constraint ck_agent_work_message_shape check (
    (
      role = 'user'
      and client_message_id is not null
      and reply_to_message_id is null
      and reasoning_content = ''
      and agent_id is null
      and agent_name_snapshot is null
      and published_version is null
      and run_id is null
      and run_status is null
      and usage_snapshot is null
      and error_snapshot is null
    )
    or
    (
      role = 'assistant'
      and client_message_id is null
      and reply_to_message_id is not null
      and agent_id is not null
      and agent_name_snapshot is not null
      and published_version is not null
      and run_id is not null
      and run_status is not null
    )
  )
);

create index if not exists idx_agent_work_message_task_created
  on agent_work_message (task_id, created_at);

create index if not exists idx_agent_work_message_task_run_status
  on agent_work_message (task_id, run_status);
