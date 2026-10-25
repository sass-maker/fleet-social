CREATE TABLE `generators` (
	`id` text PRIMARY KEY NOT NULL,
	`user_id` text NOT NULL,
	`name` text NOT NULL,
	`kind` text NOT NULL,
	`endpoint_url` text,
	`secret_enc` text,
	`lead_minutes` integer NOT NULL DEFAULT 360,
	`created_at` integer NOT NULL,
	`updated_at` integer NOT NULL,
	FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON DELETE CASCADE
);
CREATE UNIQUE INDEX `generators_user_name_uq` ON `generators` (`user_id`, `name`);
CREATE TABLE `generation_slots` (
	`id` text PRIMARY KEY NOT NULL,
	`user_id` text NOT NULL,
	`generator_id` text NOT NULL,
	`project_id` text NOT NULL,
	`title` text,
	`brief` text NOT NULL,
	`planned_for` integer NOT NULL,
	`request_at` integer NOT NULL,
	`status` text NOT NULL DEFAULT 'planned',
	`attempts` integer NOT NULL DEFAULT 0,
	`last_error` text,
	`requested_at` integer,
	`draft_id` text,
	`created_at` integer NOT NULL,
	`updated_at` integer NOT NULL,
	FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON DELETE CASCADE,
	FOREIGN KEY (`generator_id`) REFERENCES `generators`(`id`) ON DELETE CASCADE,
	FOREIGN KEY (`draft_id`) REFERENCES `drafts`(`id`) ON DELETE SET NULL
);
CREATE INDEX `generation_slots_status_request_idx` ON `generation_slots` (`status`, `request_at`);
CREATE INDEX `generation_slots_user_planned_idx` ON `generation_slots` (`user_id`, `planned_for`);
