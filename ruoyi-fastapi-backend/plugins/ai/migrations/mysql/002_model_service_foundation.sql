-- ----------------------------
-- Platform模型目录表
-- ----------------------------
create table ai_model_catalog (
  model_id             varchar(36)   not null comment '平台模型ID',
  model_code           varchar(100)  not null comment '平台模型编码',
  display_name         varchar(100)  not null comment '模型展示名称',
  model_type           varchar(32)   not null comment '模型类型',
  capabilities         json          not null comment '模型能力JSON',
  context_window       int           default null comment '上下文窗口大小',
  max_output_tokens    int           default null comment '最大输出Token数',
  default_parameters   json          not null comment '默认调用参数JSON',
  lifecycle_status     varchar(16)   not null default 'draft' comment '生命周期状态',
  published_at         datetime      default null comment '发布时间',
  create_by            varchar(64)   not null default '' comment '创建者',
  create_time          datetime      not null comment '创建时间',
  update_by            varchar(64)   not null default '' comment '更新者',
  update_time          datetime      not null comment '更新时间',
  primary key (model_id),
  constraint uk_ai_model_catalog_code unique (model_code),
  constraint ck_ai_model_catalog_lifecycle_status
    check (lifecycle_status in ('draft', 'active', 'disabled')),
  constraint ck_ai_model_catalog_context_window
    check (context_window is null or context_window > 0),
  constraint ck_ai_model_catalog_max_output_tokens
    check (max_output_tokens is null or max_output_tokens > 0)
) engine=innodb comment = 'Platform模型目录表';

-- ----------------------------
-- Platform资源绑定表
-- ----------------------------
create table ai_resource_binding (
  binding_id           varchar(36)   not null comment '绑定ID',
  resource_type        varchar(32)   not null comment '资源类型',
  resource_id          varchar(36)   not null comment '平台资源ID',
  engine_type          varchar(32)   not null comment '运行时引擎类型',
  engine_resource_id   varchar(255)  not null comment '引擎侧资源标识',
  binding_status        varchar(16)   not null default 'active' comment '绑定状态',
  engine_metadata      json          not null comment '引擎元数据JSON',
  version              int           not null default 1 comment '乐观锁版本',
  create_by            varchar(64)   not null default '' comment '创建者',
  create_time          datetime      not null comment '创建时间',
  update_by            varchar(64)   not null default '' comment '更新者',
  update_time          datetime      not null comment '更新时间',
  primary key (binding_id),
  constraint uk_ai_resource_binding_resource unique (resource_type, resource_id),
  constraint ck_ai_resource_binding_status
    check (binding_status in ('active', 'disabled')),
  constraint ck_ai_resource_binding_version
    check (version > 0),
  constraint fk_ai_resource_binding_model
    foreign key (resource_id) references ai_model_catalog (model_id) on delete restrict
) engine=innodb comment = 'Platform资源绑定表';

create index idx_ai_model_catalog_lifecycle on ai_model_catalog (lifecycle_status);
create index idx_ai_resource_binding_engine on ai_resource_binding (engine_type, engine_resource_id);
