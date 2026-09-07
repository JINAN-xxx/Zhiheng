alter table ai_chat_message add column skill_snapshots json null after images;
update ai_chat_message set skill_snapshots = json_array() where skill_snapshots is null;
alter table ai_chat_message modify column skill_snapshots json not null;

