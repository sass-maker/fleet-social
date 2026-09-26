ALTER TABLE `drafts` ADD `project_id` text;
ALTER TABLE `drafts` ADD `source_ref` text;
ALTER TABLE `drafts` ADD `approval_hash` text;
ALTER TABLE `drafts` ADD `approved_at` integer;
CREATE UNIQUE INDEX `drafts_project_source_uq` ON `drafts` (`project_id`, `source_ref`);
