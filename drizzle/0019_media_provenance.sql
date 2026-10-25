ALTER TABLE `draft_media` ADD `sha256` text;
ALTER TABLE `draft_media` ADD `provenance_json` text;
CREATE INDEX `draft_media_draft_sha_idx` ON `draft_media` (`draft_id`, `sha256`);
