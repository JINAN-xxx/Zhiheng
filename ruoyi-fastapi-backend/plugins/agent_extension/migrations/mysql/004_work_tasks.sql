create table if not exists agent_work_task (
  task_id                  varchar(36)  not null,
  owner_user_id            bigint       not null,
  create_client_message_id varchar(100) not null,
  title                    varchar(100) not null,
  goal                     mediumtext   not null,
  status                   varchar(16)  not null default 'active',
  active_agent_id          varchar(36)  null,
  version                  int          not null default 1,
  created_at               datetime     not null,
  updated_at               datetime     not null,
  primary key (task_id),
  constraint uk_agent_work_task_create_client unique (owner_user_id, create_client_message_id),
  constraint ck_agent_work_task_status check (status in ('active', 'completed', 'archived')),
  constraint ck_agent_work_task_version check (version > 0),
  index idx_agent_work_task_owner_status_updated (owner_user_id, status, updated_at)
) engine=innodb;

create table if not exists agent_work_task_member (
  task_id             varchar(36)  not null,
  agent_id            varchar(36)  not null,
  agent_name_snapshot varchar(100) not null,
  published_version   int          not null,
  config_snapshot     json         not null,
  added_at            datetime     not null,
  removed_at          datetime     null,
  primary key (task_id, agent_id),
  constraint fk_agent_work_task_member_task foreign key (task_id)
    references agent_work_task (task_id) on delete restrict,
  constraint ck_agent_work_task_member_published_version check (published_version > 0),
  index idx_agent_work_task_member_task_removed (task_id, removed_at)
) engine=innodb;

create table if not exists agent_work_message (
  message_id          varchar(36)  not null,
  task_id             varchar(36)  not null,
  client_message_id   varchar(100) null,
  reply_to_message_id varchar(36)  null,
  role                varchar(16)  not null,
  content             mediumtext   not null,
  reasoning_content   mediumtext   not null default (''),
  agent_id            varchar(36)  null,
  agent_name_snapshot varchar(100) null,
  published_version   int          null,
  run_id              varchar(36)  null,
  run_status          varchar(16)  null,
  usage_snapshot      json         null,
  error_snapshot      json         null,
  created_at          datetime     not null,
  primary key (message_id),
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
  ),
  index idx_agent_work_message_task_created (task_id, created_at),
  index idx_agent_work_message_task_run_status (task_id, run_status)
) engine=innodb;
