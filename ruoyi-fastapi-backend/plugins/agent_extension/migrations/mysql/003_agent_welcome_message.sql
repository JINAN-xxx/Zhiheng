alter table agent_configuration
  add column welcome_message varchar(2000) not null default '';
