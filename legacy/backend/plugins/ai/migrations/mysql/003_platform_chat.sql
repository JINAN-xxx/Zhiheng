-- ----------------------------
-- Platform 对话会话表
-- ----------------------------
create table if not exists ai_chat_session (
  session_id  varchar(36)  not null comment 'Platform Session ID',
  user_id     bigint       not null comment '平台用户ID',
  model_id    varchar(36)  not null comment '锁定的 Platform Model ID',
  title       varchar(200) not null default '' comment '会话标题',
  status      varchar(16)  not null default 'active' comment '会话状态',
  created_at  datetime     not null comment '创建时间',
  updated_at  datetime     not null comment '更新时间',
  primary key (session_id),
  constraint ck_ai_chat_session_status check (status in ('active', 'archived')),
  constraint fk_ai_chat_session_model
    foreign key (model_id) references ai_model_catalog (model_id) on delete restrict,
  index idx_ai_chat_session_user_updated (user_id, updated_at)
) engine=innodb comment='Platform 对话会话表';

-- ----------------------------
-- Platform 对话消息表
-- ----------------------------
create table if not exists ai_chat_message (
  message_id        varchar(36)  not null comment 'Platform Message ID',
  session_id        varchar(36)  not null comment 'Platform Session ID',
  client_message_id varchar(100) not null comment '客户端幂等消息ID',
  role              varchar(16)  not null comment '消息角色',
  content           text         not null comment '消息正文',
  reasoning_content text         default null comment '推理内容',
  images            json         not null comment 'Platform 文件引用JSON',
  status            varchar(16)  not null comment '消息状态',
  created_at        datetime     not null comment '创建时间',
  updated_at        datetime     not null comment '更新时间',
  primary key (message_id),
  constraint uk_ai_chat_message_client unique (session_id, client_message_id),
  constraint ck_ai_chat_message_role check (role in ('user', 'assistant')),
  constraint ck_ai_chat_message_status check (status in ('complete', 'interrupted', 'cancelled')),
  constraint fk_ai_chat_message_session
    foreign key (session_id) references ai_chat_session (session_id) on delete restrict,
  index idx_ai_chat_message_session_created (session_id, created_at)
) engine=innodb comment='Platform 对话消息表';

-- ----------------------------
-- Platform 对话运行表
-- ----------------------------
create table if not exists ai_chat_run (
  run_id                 varchar(36)    not null comment 'Platform Run ID',
  session_id             varchar(36)    not null comment 'Platform Session ID',
  model_id               varchar(36)    not null comment 'Platform Model 快照',
  binding_id             varchar(36)    not null comment 'Platform Binding 快照',
  engine_type            varchar(32)    not null comment '运行时引擎类型',
  engine_model_snapshot  varchar(255)   not null comment '引擎模型快照',
  engine_request_id      varchar(255)   default null comment '引擎请求ID',
  trace_id               varchar(128)   default null comment 'Platform Trace ID',
  status                 varchar(16)    not null default 'pending' comment '运行状态',
  input_tokens           int            default null comment '输入Token数',
  output_tokens          int            default null comment '输出Token数',
  reasoning_tokens       int            default null comment '推理Token数',
  cache_read_tokens      int            default null comment '缓存读取Token数',
  cache_write_tokens     int            default null comment '缓存写入Token数',
  raw_cost               decimal(20,10) default null comment '引擎原始成本',
  error_code             varchar(64)    default null comment 'Platform 脱敏错误码',
  error_message          varchar(500)   default null comment 'Platform 脱敏错误文案',
  started_at             datetime       default null comment '开始时间',
  finished_at            datetime       default null comment '结束时间',
  created_at             datetime       not null comment '创建时间',
  updated_at             datetime       not null comment '更新时间',
  primary key (run_id),
  constraint ck_ai_chat_run_status
    check (status in ('pending', 'running', 'completed', 'cancelled', 'interrupted', 'failed')),
  constraint fk_ai_chat_run_session
    foreign key (session_id) references ai_chat_session (session_id) on delete restrict,
  constraint fk_ai_chat_run_model
    foreign key (model_id) references ai_model_catalog (model_id) on delete restrict,
  constraint fk_ai_chat_run_binding
    foreign key (binding_id) references ai_resource_binding (binding_id) on delete restrict,
  index idx_ai_chat_run_session_created (session_id, created_at),
  index idx_ai_chat_run_status (status)
) engine=innodb comment='Platform 对话运行表';

-- ----------------------------
-- Platform 对话用户偏好表
-- ----------------------------
create table if not exists ai_chat_preferences (
  preference_id         bigint       not null auto_increment comment '偏好ID',
  user_id               bigint       not null comment '平台用户ID',
  temperature           double       default null comment '默认温度',
  add_history           boolean      not null default true comment '是否附带历史',
  history_message_count int          not null default 20 comment '历史消息数量',
  system_prompt          text         default null comment '系统提示词',
  metrics_visible       boolean      not null default true comment '是否展示指标',
  vision_enabled        boolean      not null default true comment '是否启用视觉',
  image_max_size_mb     int          not null default 10 comment '图片大小上限MB',
  created_at            datetime     not null comment '创建时间',
  updated_at            datetime     not null comment '更新时间',
  primary key (preference_id),
  constraint uk_ai_chat_preferences_user unique (user_id)
) engine=innodb comment='Platform 对话用户偏好表';
