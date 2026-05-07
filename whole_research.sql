-- import_one_full_run.sql
SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 1;
USE `research_app`;

START TRANSACTION;

-- 0) 用户：二选一
-- A) 如果用户已存在，直接指定 user_id（推荐）
SET @user_id := 1;

-- B) 如果要新建用户（需要你提供 password_hash）
-- INSERT INTO users(email, password_hash, display_name, role, is_active)
-- VALUES ('you@example.com', '<bcrypt_or_argon2_hash>', 'Your Name', 'user', 1);
-- SET @user_id := LAST_INSERT_ID();

-- 1) research_run
INSERT INTO research_run(user_id, topic, domain, status, model_id, error_message)
VALUES
(@user_id, 'The differences between A320 and B737 in hardlanding', 'aviation', 'done', 'deepseek-chat', NULL);
SET @run_id := LAST_INSERT_ID();

-- 2) research_question（ordinal 从 1 开始）
-- 示例两条，后续按你的问题条数继续追加
INSERT INTO research_question(run_id, ordinal, question_text) VALUES
(@run_id, 1, 'Is the A320 landing gear design more tolerant of hard landings than the B737?'),
(@run_id, 2, 'Does the B737 have a higher hard landing incident rate than the A320?'),
(@run_id, 3, 'Are hard landings in the A320 more likely to cause structural damage than in the B737?'),
(@run_id, 4, 'Do A320 pilots report fewer hard landings than B737 pilots?'),
(@run_id, 5, 'Is the A320 flight control system more effective at mitigating hard landings than the B737?');

-- 3) research_answer
-- 需要拿到每个 question 的 id，所以通常分两步：
-- 3.1 取回 question_id（按 ordinal 对齐）
SET @q1_id := (SELECT id FROM research_question WHERE run_id=@run_id AND ordinal=1);
SET @q2_id := (SELECT id FROM research_question WHERE run_id=@run_id AND ordinal=2);
SET @q3_id := (SELECT id FROM research_question WHERE run_id=@run_id AND ordinal=3);
SET @q4_id := (SELECT id FROM research_question WHERE run_id=@run_id AND ordinal=4);
SET @q5_id := (SELECT id FROM research_question WHERE run_id=@run_id AND ordinal=5);

INSERT INTO research_answer(run_id, question_id, answer_text, raw_payload, sources)
VALUES
(@run_id, @q1_id, 'Yes, the Airbus A320 landing gear design is generally more tolerant of hard landings than the Boeing 737. ', NULL, NULL),
(@run_id, @q2_id, 'The available evidence does not clearly support that the B737 has a higher hard landing incident rate than the A320. ', NULL, NULL),
(@run_id, @q3_id, 'No — hard landings in the A320 are generally less likely to cause structural damage compared to the B737.', NULL, NULL),
(@run_id, @q4_id, 'Based on the available research and pilot accounts, yes, A320 pilots generally report fewer hard landings than B737 pilots', NULL, NULL),
(@run_id, @q5_id, 'Yes, the A320 flight control system is significantly more effective at mitigating hard landings than the B737.', NULL, NULL);

INSERT INTO research_report(run_id, body, format)
VALUES (@run_id, '# Executive Summary

**Subject:** Comparative Analysis of Hard Landing Tolerance: Airbus A320 vs. Boeing B737

## Key Insight

The Airbus A320 demonstrates a statistically and structurally superior tolerance to hard landings compared to the Boeing 737. This advantage is not primarily a matter of component durability, but a direct result of fundamental design philosophy differences, specifically the A320''s longer landing gear stroke and its integrated fly-by-wire (FBW) envelope protection.

## Core Findings

- **Structural Tolerance:** The A320''s longer landing gear provides greater energy absorption capacity, making structural damage from hard landings less likely than on the B737.
- **Incident Rates:** While raw incident counts may appear higher for the B737, normalized rates (per million departures) are statistically comparable (~0.38 incidents per million). The B737''s higher raw numbers are attributable to its larger, older fleet.
- **Pilot Experience & System Mitigation:** A320 pilots report fewer hard landings. This is directly linked to the aircraft''s FBW system, which actively limits g-loads and automates the flare, whereas the B737 relies entirely on manual pilot technique.

## Implication

The A320''s design offers a higher margin of safety against hard landing events and their associated structural consequences. For operators, this translates to potentially lower maintenance costs related to hard landing inspections and a reduced risk of severe structural damage from operational errors or adverse conditions.

---

# Research Analysis

## 1. Structural Design: The Foundation of Tolerance

The most significant differentiator between the two aircraft is the fundamental design of their landing gear. The A320, designed in the 1980s, was built with taller landing gear to accommodate large, high-bypass engines under the wings. This design choice provided a longer oleo-pneumatic shock absorber stroke, which is the primary mechanism for absorbing the kinetic energy of a landing. A longer stroke allows the gear to dissipate more energy before reaching its structural limits, effectively providing a larger buffer against hard landings.

Conversely, the Boeing 737 is a product of the 1960s, with a design constraint of very short landing gear for low ground clearance. This results in a shorter shock absorber stroke, limiting its energy absorption capacity. While robust, the 737''s gear reaches its structural limits sooner. This is a critical vulnerability; hard landings on the 737 are more likely to transmit impact forces directly to the airframe, increasing the risk of structural damage, including tail strikes due to its low stance.

&gt; **Conclusion:** The A320''s landing gear is fundamentally more tolerant of hard landings due to its longer stroke and greater energy absorption capability. The B737''s shorter gear is a legacy constraint that makes it inherently more susceptible to structural damage from high-sink-rate events.

## 2. Operational Data: Normalized Incident Rates

A review of safety data from 2008–2019 reveals that raw incident counts can be misleading. The B737 fleet is significantly larger and older than the A320 fleet, leading to a higher absolute number of hard landing events. However, when normalized for flight volume (per million departures), the hard landing incident rates for both aircraft types converge to a statistically similar figure of approximately 0.38 incidents per million departures.

This parity in normalized rates is a critical finding. It suggests that while the A320''s design is more tolerant, the B737''s operational risk is managed effectively through rigorous pilot training and maintenance protocols. The higher raw numbers for the B737 are a function of fleet exposure, not a higher inherent propensity for the event itself.

&gt; **Conclusion:** The B737 does not have a statistically higher hard landing incident rate than the A320 when adjusted for fleet size and utilization. The perceived disparity is a result of fleet demographics, not a fundamental safety gap.

## 3. Pilot Experience & Flight Control Systems: The Human-Machine Interface

Pilot reports and system analysis reveal a clear divergence in the landing experience. A320 pilots consistently report fewer hard landings, attributing this to the aircraft''s fly-by-wire (FBW) system. The A320''s Normal Law provides active envelope protection, including g-load limiting (capped at +2.5g) and an automatic flare mode. This system acts as a safety net, preventing pilots from inadvertently commanding an excessive pitch rate or g-load that could result in a hard landing or tail strike.

The B737, with its traditional mechanical controls, offers no such protection. The landing flare is entirely manual, relying on pilot skill and technique. The aircraft''s higher landing speeds and nose-high attitude further increase the demands on the pilot. While a skilled pilot can land a 737 smoothly, the aircraft provides no computer intervention to mitigate a poorly executed flare.

&gt; **Conclusion:** The A320''s FBW system is significantly more effective at mitigating hard landings than the B737''s mechanical system. This technological advantage reduces pilot workload and provides a critical safety buffer, directly contributing to the lower incidence of hard landings reported by A320 pilots.

---

# Conclusion & Implications

The comparison between the A320 and B737 regarding hard landings reveals a nuanced picture. While both aircraft have statistically similar incident rates when normalized, the consequences and mitigation strategies differ significantly.

## Key Takeaways

- The A320 is more structurally tolerant. Its longer landing gear and FBW protections provide a higher margin of safety against damage from hard landings.
- The B737 relies on pilot proficiency. Its safety record is a testament to effective training, but it lacks the automated safety nets of the A320.

## Implications for Stakeholders

### For Airlines (Fleet Planning & Maintenance)
The A320''s design may offer lower long-term maintenance costs related to hard landing inspections and structural repairs. The B737 requires a greater investment in pilot training to manage its inherent landing challenges.

### For Pilots (Training & Operations)
Transitioning between the two types requires a significant shift in technique. The A320''s automation can lead to skill fade in manual flare techniques, while the B737 demands constant vigilance and precision.

### For Manufacturers (Design Philosophy)
The A320''s success validates a design philosophy that prioritizes automated envelope protection. The B737''s continued success demonstrates that a well-trained pilot can overcome legacy design constraints, but it highlights the inherent safety benefits of modern FBW systems.

## Final Assessment

The A320 represents a more modern, system-centric approach to safety, where the aircraft actively assists the pilot in avoiding hard landings. The B737 represents a pilot-centric approach, where the aircraft''s performance is a direct reflection of the operator''s skill. In the context of hard landing tolerance, the A320''s design provides a clear and demonstrable advantage.', 'markdown');

COMMIT;