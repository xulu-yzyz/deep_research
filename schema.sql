-- Deep Research App: users + research persistence (MySQL 8.0, utf8mb4)
-- Database name must match MYSQL_DATABASE in docker-compose (e.g. research_app)

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 1;

USE `research_app`;

-- ---------------------------------------------------------------------------
-- Users (login)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `users` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `email` VARCHAR(255) NOT NULL COMMENT 'login id, unique',
  `password_hash` VARCHAR(255) NOT NULL COMMENT 'bcrypt/argon2 hash, never store plain password',
  `display_name` VARCHAR(100) NULL DEFAULT NULL,
  `role` ENUM('user', 'admin') NOT NULL DEFAULT 'user',
  `is_active` TINYINT(1) NOT NULL DEFAULT 1 COMMENT '0 = disabled login',
  `last_login_at` DATETIME(6) NULL DEFAULT NULL,
  `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_users_email` (`email`),
  KEY `idx_users_active` (`is_active`),
  KEY `idx_users_created` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='Registered users';

-- Optional: refresh tokens for JWT / long-lived sessions (revocable in DB)
CREATE TABLE IF NOT EXISTS `user_refresh_tokens` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `user_id` BIGINT UNSIGNED NOT NULL,
  `token_hash` CHAR(64) NOT NULL COMMENT 'SHA-256 hex of refresh token',
  `expires_at` DATETIME(6) NOT NULL,
  `revoked_at` DATETIME(6) NULL DEFAULT NULL COMMENT 'set when user logs out or rotates token',
  `user_agent` VARCHAR(512) NULL DEFAULT NULL,
  `ip_address` VARCHAR(45) NULL DEFAULT NULL COMMENT 'IPv4 or IPv6 text',
  `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_refresh_token_hash` (`token_hash`),
  KEY `idx_refresh_user` (`user_id`),
  KEY `idx_refresh_expires` (`expires_at`),
  CONSTRAINT `fk_refresh_user`
    FOREIGN KEY (`user_id`) REFERENCES `users` (`id`)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='Refresh tokens (optional; pair with Redis for access token blacklist if needed)';

-- ---------------------------------------------------------------------------
-- Research runs (one end-to-end job per user)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `research_run` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `user_id` BIGINT UNSIGNED NOT NULL,
  `topic` VARCHAR(512) NOT NULL,
  `domain` VARCHAR(256) NOT NULL,
  `status` VARCHAR(32) NOT NULL DEFAULT 'draft'
    COMMENT 'draft|questions_ready|researching|compiling|done|failed',
  `model_id` VARCHAR(128) NULL DEFAULT NULL COMMENT 'e.g. deepseek-chat',
  `error_message` TEXT NULL DEFAULT NULL,
  `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  KEY `idx_run_user_created` (`user_id`, `created_at`),
  KEY `idx_run_status` (`status`),
  CONSTRAINT `fk_run_user`
    FOREIGN KEY (`user_id`) REFERENCES `users` (`id`)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='One research session from topic to report';

CREATE TABLE IF NOT EXISTS `research_question` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `run_id` BIGINT UNSIGNED NOT NULL,
  `ordinal` INT UNSIGNED NOT NULL COMMENT '1-based order in UI',
  `question_text` TEXT NOT NULL,
  `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_run_ordinal` (`run_id`, `ordinal`),
  KEY `idx_question_run` (`run_id`),
  CONSTRAINT `fk_question_run`
    FOREIGN KEY (`run_id`) REFERENCES `research_run` (`id`)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='Generated sub-questions for a run';

CREATE TABLE IF NOT EXISTS `research_answer` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `run_id` BIGINT UNSIGNED NOT NULL,
  `question_id` BIGINT UNSIGNED NOT NULL,
  `answer_text` LONGTEXT NOT NULL COMMENT 'sanitized text shown to user',
  `raw_payload` JSON NULL DEFAULT NULL COMMENT 'optional raw model/tool payload for debugging',
  `sources` JSON NULL DEFAULT NULL COMMENT 'optional list of URLs/citations',
  `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  `updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_answer_question` (`question_id`),
  KEY `idx_answer_run` (`run_id`),
  CONSTRAINT `fk_answer_run`
    FOREIGN KEY (`run_id`) REFERENCES `research_run` (`id`)
    ON DELETE CASCADE,
  CONSTRAINT `fk_answer_question`
    FOREIGN KEY (`question_id`) REFERENCES `research_question` (`id`)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='Answer per question (one row per question)';

CREATE TABLE IF NOT EXISTS `research_report` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `run_id` BIGINT UNSIGNED NOT NULL,
  `body` LONGTEXT NOT NULL,
  `format` ENUM('html', 'markdown') NOT NULL DEFAULT 'html',
  `created_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_report_run` (`run_id`),
  CONSTRAINT `fk_report_run`
    FOREIGN KEY (`run_id`) REFERENCES `research_run` (`id`)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='Final compiled report for a run';

CREATE TABLE IF NOT EXISTS `user_preference_profile` (
  `user_id` BIGINT UNSIGNED NOT NULL,
  `preferences_json` JSON NOT NULL,
  `source` ENUM('manual', 'inferred') NOT NULL DEFAULT 'manual',
  `updated_at` DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (`user_id`),
  CONSTRAINT `fk_preference_user`
    FOREIGN KEY (`user_id`) REFERENCES `users` (`id`)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='User-level long-term preferences for report generation';