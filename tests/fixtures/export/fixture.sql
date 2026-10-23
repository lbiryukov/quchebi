-- Synthetic mini-Tbilisi for export/validate tests. Applied after migrations/001_init.sql.
-- Ids are deliberately sparse so that the export must re-map every reference to an index.
-- Expected golden counts (no zone) are asserted in tests/test_validate_golden.py.

INSERT INTO meta(key, value) VALUES ('osm_snapshot', '2026-10-23T19:42:11Z');

-- ------------------------------------------------------------------ areas
INSERT INTO area(id, kind, ka, ru, en, parent_id, osm_type, osm_id, area_km2, lat, lon, geom_wkt) VALUES
 (11, 'district', 'მთაწმინდის რაიონი', 'Мтацминда', 'Mtatsminda', NULL, 'r', 1001, 4.2, 41.69, 44.79,
  'POLYGON((44.78 41.68, 44.81 41.68, 44.81 41.70, 44.78 41.70, 44.78 41.68))'),
 (12, 'district', 'ვაკის რაიონი', 'Ваке', 'Vake', NULL, 'r', 1002, 3.1, 41.71, 44.76,
  'MULTIPOLYGON(((44.74 41.70, 44.78 41.70, 44.78 41.72, 44.74 41.72, 44.74 41.70),(44.75 41.705, 44.752 41.705, 44.752 41.707, 44.75 41.707, 44.75 41.705)))'),
 (13, 'neighbourhood', 'სოლოლაკი', 'Сололаки', 'Sololaki', 11, 'w', 2001, 0.4912, 41.6885, 44.79,
  'POLYGON((44.785 41.685, 44.795 41.685, 44.795 41.692, 44.785 41.692, 44.785 41.685))'),
 (14, 'quarter', 'ვერა', 'Вера', 'Vera', 12, 'n', 2002, NULL, 41.708, 44.784, NULL);

-- ------------------------------------------------------------------ eponyms
INSERT INTO eponym(id, qid, key, kind, ka, ru, en, gender, birth_year, death_year, origin, short_ru, occupations_ru, confidence, source) VALUES
 (21, 'Q144086', NULL, 'person', 'გალაკტიონ ტაბიძე', 'Галактион Табидзе', 'Galaktion Tabidze', 'm', 1891, 1959, 'georgian', 'грузинский поэт', '["поэт"]', 0.98, 'osm'),
 (22, 'Q166232', NULL, 'person', 'ილია ჭავჭავაძე', 'Илья Чавчавадзе', 'Ilia Chavchavadze', 'm', 1837, 1907, 'georgian', 'писатель и общественный деятель', '["писатель","поэт","публицист"]', 0.95, 'wd_p138'),
 (23, NULL, 'anastasia-eristavi', 'person', 'ანასტასია ერისთავი-ხოშტარია', 'Анастасия Эристави-Хоштария', NULL, 'f', 1868, 1951, 'georgian', 'писательница', '["писательница"]', 0.8, 'llm'),
 (24, NULL, 'barbare-jorjadze', 'person', 'ბარბარე ჯორჯაძე', 'Барбаре Джорджадзе', NULL, 'f', 1833, 1895, 'georgian', NULL, NULL, 0.7, 'rules'),
 (25, 'Q178036', NULL, 'toponym', 'ყაზბეგი', 'Казбек', 'Kazbek', NULL, NULL, NULL, NULL, 'гора', NULL, 0.9, 'wd_match'),
 (26, NULL, '26-maisi', 'event_date', '26 მაისი', '26 мая', NULL, NULL, NULL, NULL, NULL, 'День независимости', NULL, 0.99, 'rules'),
 (27, NULL, 'zubalashvili', 'persons_multi', 'ზუბალაშვილები', 'Зубалашвили', NULL, NULL, NULL, NULL, 'georgian', 'меценаты', '["меценат"]', 0.9, 'manual'),
 (28, NULL, 'unreferenced', 'person', 'არავინ', 'Никто', NULL, 'm', NULL, NULL, NULL, NULL, NULL, 0.5, 'rules'),
 (29, 'Q1001', NULL, 'person', 'ლენინი', 'Ленин', 'Lenin', 'm', 1870, 1924, 'foreign', 'революционер', '["революционер"]', 0.99, 'wd_match');

INSERT INTO eponym_category(eponym_id, category, rank, source) VALUES
 (21, 'lit.poet', 0, 'osm'),
 (22, 'lit.writer', 0, 'wd'), (22, 'lit.poet', 1, 'wd'), (22, 'power.public', 2, 'wd'),
 (23, 'lit.writer', 0, 'llm'),
 (24, 'lit.writer', 0, 'llm'),
 (25, 'place.mountain', 0, 'rules'),
 (26, 'event.date', 0, 'rules'),
 (27, 'biz.patron', 0, 'rules'),
 (29, 'power.revolutionary', 0, 'manual'), (29, 'bogus.cat', 1, 'manual');

-- ------------------------------------------------------------------ families
INSERT INTO family(id, key, base_ka, base_ru, base_en, eponym_nom_ka, kind_hint, eponym_id) VALUES
 (31, 'galaktion-tabidze', 'გალაკტიონ ტაბიძის', 'Галактиона Табидзе', 'Galaktion Tabidze', 'გალაკტიონ ტაბიძე', 'person', 21),
 (32, 'ilia-chavchavadze', 'ილია ჭავჭავაძის', 'Ильи Чавчавадзе', 'Ilia Chavchavadze', 'ილია ჭავჭავაძე', 'person', 22),
 (33, 'anastasia-eristavi', 'ანასტასია ერისთავის', 'Анастасии Эристави', NULL, NULL, 'person', 23),
 (34, 'barbare-jorjadze', 'ბარბარე ჯორჯაძის', 'Барбаре Джорджадзе', NULL, NULL, 'person', 24),
 (35, 'kazbegi', 'ყაზბეგის', 'Казбеги', NULL, NULL, 'toponym', 25),
 (36, '26-maisi', '26 მაისის', '26 Мая', NULL, NULL, 'event_date', 26),
 (37, 'zubalashvili', 'ზუბალაშვილების', 'Зубалашвили', NULL, NULL, 'persons_multi', 27),
 (38, 'lado-asatiani', 'ლადო ასათიანის', 'Ладо Асатиани', NULL, NULL, 'person', NULL),
 (39, 'unused-family', 'არაფრის', NULL, NULL, NULL, NULL, NULL);

-- ------------------------------------------------------------------ features
INSERT INTO feature(id, kind, ka, ru, en, ord, family_id, parent_id, len_m, lat, lon, bbox, geom_wkt, area_id, osm_ids, wikidata) VALUES
 (101, 'street', 'გალაკტიონ ტაბიძის ქუჩა', 'улица Галактиона Табидзе', 'Galaktion Tabidze Street', NULL, 31, NULL, 832.4, 41.695, 44.795, '41.695,44.79,41.695,44.8',
  'MULTILINESTRING((44.79 41.695, 44.7925 41.69501, 44.795 41.695),(44.795 41.695, 44.80 41.695))', NULL, '["w1","w2"]', 'Q144086'),
 (103, 'avenue', 'ილია ჭავჭავაძის გამზირი', 'проспект Ильи Чавчавадзе', 'Ilia Chavchavadze Avenue', NULL, 32, NULL, 1112.0, 41.695, 44.795, NULL,
  'LINESTRING(44.795 41.69, 44.795 41.70)', NULL, '["w3"]', NULL);
INSERT INTO feature(id, kind, ka, ru, en, ord, family_id, parent_id, len_m, lat, lon, bbox, geom_wkt, area_id, osm_ids, wikidata) VALUES
 (102, 'lane', 'გალაკტიონ ტაბიძის I შესახვევი', '1-й переулок Галактиона Табидзе', NULL, 1, 31, 101, NULL, NULL, NULL, NULL,
  'LINESTRING(44.794 41.692, 44.797 41.692)', NULL, '["w4"]', NULL),
 (104, 'lane', 'ილია ჭავჭავაძის შესახვევი', 'переулок Ильи Чавчавадзе', NULL, NULL, 32, 103, 589.0, 41.697, 44.797, NULL,
  'LINESTRING(44.795 41.697, 44.798 41.697, 44.798 41.694)', NULL, '["w5"]', NULL),
 (105, 'street', 'ანასტასია ერისთავის ქუჩა', 'улица Анастасии Эристави', NULL, NULL, 33, NULL, 830.0, 41.705, 44.785, NULL,
  'LINESTRING(44.78 41.705, 44.79 41.705)', NULL, '["w6"]', NULL),
 (106, 'lane', 'ლადო ასათიანის II შესახვევი', '2-й переулок Ладо Асатиани', 'Lado Asatiani 2nd Lane', 2, 38, NULL, 167.0, 41.6952, 44.792, NULL,
  'LINESTRING(44.792 41.6945, 44.792 41.696)', NULL, '["w7"]', NULL),
 (107, 'lane', 'ანასტასია ერისთავის შესახვევი', 'переулок Анастасии Эристави', NULL, NULL, 33, 105, 222.0, 41.704, 44.785, NULL,
  'LINESTRING(44.785 41.703, 44.785 41.705)', NULL, '["w8"]', NULL),
 (108, 'dead_end', 'ბარბარე ჯორჯაძის ჩიხი', 'тупик Барбаре Джорджадзе', NULL, NULL, 34, NULL, 111.0, 41.7065, 44.781, NULL,
  'LINESTRING(44.781 41.706, 44.781 41.707)', NULL, '["w9"]', NULL),
 (109, 'dead_end', 'ყაზბეგის ჩიხი', 'тупик Казбеги', NULL, NULL, 35, NULL, 111.0, 41.7065, 44.782, NULL,
  'LINESTRING(44.782 41.706, 44.782 41.707)', NULL, '["w10"]', NULL),
 (110, 'dead_end', 'კაკლის ჩიხი', NULL, NULL, NULL, NULL, NULL, 111.0, 41.7065, 44.783, NULL,
  'LINESTRING(44.783 41.706, 44.783 41.707)', NULL, '["w11"]', NULL),
 (111, 'street', '26 მაისის ქუჩა', 'улица 26 Мая', NULL, NULL, 36, NULL, 415.0, 41.71, 44.7725, NULL,
  'LINESTRING(44.77 41.71, 44.775 41.71)', NULL, '["w12"]', NULL),
 (112, 'street', 'ზუბალაშვილების ქუჩა', 'улица Братьев Зубалашвили', NULL, NULL, 37, NULL, 250.0, 41.69, 44.7875, NULL,
  'LINESTRING(44.786 41.69, 44.789 41.69)', NULL, '["w13"]', NULL),
 (113, 'street', 'ლესელიძის ქუჩა', NULL, 'Leselidze Street', NULL, NULL, NULL, 190.0, 41.6905, 44.807, NULL,
  'LINESTRING(44.806 41.69, 44.808 41.691)', NULL, '["w14"]', NULL),
 (114, 'square', 'თავისუფლების მოედანი', 'площадь Свободы', 'Freedom Square', NULL, NULL, NULL, 0, 41.6935, 44.8015, NULL,
  'POLYGON((44.801 41.693, 44.802 41.693, 44.802 41.694, 44.801 41.694, 44.801 41.693))', NULL, '["w15"]', NULL),
 (115, 'metro', 'თავისუფლების მოედანი', 'Площадь Свободы', NULL, NULL, NULL, NULL, 0, 41.6935, 44.8015, NULL,
  'POINT(44.8015 41.6935)', NULL, '["n16"]', NULL),
 (116, 'district', 'მთაწმინდის რაიონი', 'район Мтацминда', NULL, NULL, NULL, NULL, 0, 41.69, 44.79, NULL,
  'POINT(44.79 41.69)', 11, '["r1001"]', NULL),
 (117, 'neighbourhood', 'სოლოლაკი', 'Сололаки', NULL, NULL, NULL, NULL, 0, 41.6885, 44.79, NULL,
  'POINT(44.79 41.6885)', 13, '["w2001"]', NULL),
 (118, 'river', 'მტკვარი', 'Кура', 'Mtkvari', NULL, NULL, NULL, NULL, NULL, NULL, NULL,
  'MULTILINESTRING((44.80 41.68, 44.805 41.69),(44.806 41.692, 44.81 41.70),(44.805 41.69, 44.8055 41.691))', NULL, '["w17","w18","w19"]', NULL),
 (119, 'bridge', 'მეტეხის ხიდი', 'Метехский мост', NULL, NULL, NULL, NULL, 520.0, 41.685, 44.803, NULL,
  'LINESTRING(44.80 41.686, 44.806 41.684)', NULL, '["w20"]', NULL),
 (120, 'passage', 'ვაკის გასასვლელი', 'проезд Ваке', NULL, NULL, NULL, NULL, 83.0, 41.711, 44.7765, NULL,
  'LINESTRING(44.776 41.711, 44.777 41.711)', NULL, '["w21"]', NULL),
 (121, 'other', 'უცნობი ობიექტი', NULL, NULL, NULL, NULL, NULL, NULL, 41.70, 44.80, NULL, NULL, NULL, '["n22"]', NULL),
 (122, 'foo', 'უცნაური სახეობა', NULL, NULL, NULL, NULL, NULL, 10.0, 41.701, 44.801, NULL,
  'LINESTRING(44.801 41.701, 44.8011 41.701)', NULL, '["w23"]', NULL);

INSERT INTO feature_area(feature_id, area_id, share) VALUES
 (101, 11, 1.0), (102, 11, 1.0), (103, 11, 0.6), (103, 12, 0.4), (104, 11, 1.0), (106, 11, 1.0),
 (112, 11, 1.0), (113, 11, 1.0), (114, 11, 1.0), (115, 11, 1.0), (116, 11, 1.0), (118, 11, 0.5), (119, 11, 1.0),
 (105, 12, 1.0), (107, 12, 1.0), (108, 12, 1.0), (109, 12, 1.0), (110, 12, 1.0), (111, 12, 1.0), (120, 12, 1.0),
 (112, 13, 1.0), (117, 13, 1.0), (105, 14, 0.3);

INSERT INTO feature_name(feature_id, lang, name, kind, source) VALUES
 (101, 'en', 'Galaktion Tabidze St', 'alt', 'osm'),
 (101, 'ka', 'გ. ტაბიძის ქუჩა', 'official', 'napr'),
 (101, 'lat', 'Tabidze street', 'addr_variant', 'addr'),
 (101, 'ru', 'улица Галактиона Табидзе', 'alt', 'osm'),
 (113, 'ru', 'Армянская', 'old', 'osm'),
 (113, 'ka', 'სომხის ქუჩა', 'old', 'osm');

INSERT INTO name_history(feature_id, family_id, name_ka, name_ru, name_lat, from_year, to_year, eponym_id, source, ref, confidence) VALUES
 (113, NULL, NULL, 'улица Ленина', NULL, 1921, 1990, 29, 'enc2008', 'p. 123', 0.9),
 (113, NULL, NULL, 'улица Ленина', NULL, NULL, NULL, NULL, 'ruwiki', NULL, 0.8),
 (113, NULL, NULL, 'Армянская', NULL, NULL, 1921, NULL, 'kawiki', NULL, 0.7),
 (113, NULL, NULL, 'улица Пушкина', NULL, 1990, 1991, NULL, 'enc2008_llm', NULL, 0.3),
 (NULL, 36, NULL, 'улица Розы Люксембург', NULL, 1925, 1990, NULL, 'enc2008', NULL, 0.9),
 (103, 32, 'ჭავჭავაძის ქუჩა', NULL, 'Chavchavadze st', 1950, 1960, 22, 'kawiki', NULL, NULL);

-- ------------------------------------------------------------------ crossings (a < b by db id)
INSERT INTO crossing(a, b, lat, lon, kind, dir_a, dir_b) VALUES
 (101, 103, 41.695, 44.795, 'node', 68, 17),
 (102, 103, 41.692, 44.795, 'node', 68, 17),
 (101, 104, 41.695, 44.798, 'node', 68, 17),
 (103, 104, 41.697, 44.795, 'node', 17, 4),
 (101, 106, 41.695, 44.792, 'node', 68, 17),
 (105, 107, 41.705, 44.785, 'node', 68, 16),
 (118, 119, 41.68514, 44.80257, 'geom', 17, 68);

INSERT INTO near_pair(a, b, dist_m) VALUES (102, 106, 15.0), (108, 109, 12.0);

-- ------------------------------------------------------------------ houses
INSERT INTO house(id, feature_id, area_id, raw, n, lit, sub, n_to, corp, kind, lat, lon, addr_street, match) VALUES
 (1, 101, NULL, '1', 1, '', '', NULL, NULL, 'int', 41.69505, 44.7905, 'გალაკტიონ ტაბიძის ქუჩა', 'exact'),
 (2, 101, NULL, '3', 3, '', '', NULL, NULL, 'int', 41.69505, 44.7910, 'გალაკტიონ ტაბიძის ქუჩა', 'exact'),
 (3, 101, NULL, '3ა', 3, 'ა', '', NULL, NULL, 'letter', 41.69505, 44.7912, 'გალაკტიონ ტაბიძის ქუჩა', 'exact'),
 (4, 101, NULL, '5', 5, NULL, NULL, NULL, NULL, 'int', 41.69505, 44.7915, 'ტაბიძის ქუჩა', 'fuzzy'),
 (5, 101, NULL, '9', 9, '', '', NULL, NULL, 'int', NULL, NULL, 'გალაკტიონ ტაბიძის ქუჩა', 'exact'),
 (6, 103, NULL, '1-5', 1, '', '', 5, NULL, 'range', 41.6905, 44.79505, 'ილია ჭავჭავაძის გამზირი', 'exact'),
 (7, 103, NULL, '7', 7, '', '', NULL, NULL, 'int', 41.6910, 44.79505, 'ილია ჭავჭავაძის გამზირი', 'exact'),
 (8, 105, NULL, '3/16', 3, '', '16', NULL, NULL, 'fraction', 41.70505, 44.7810, 'ანასტასია ერისთავის ქუჩა', 'exact'),
 (9, 105, NULL, '16 კორპ. 3', 16, '', '', NULL, '3', 'corp', 41.70505, 44.7850, 'ანასტასია ერისთავის ქუჩა', 'exact'),
 (10, 107, NULL, '1;3', 1, '', '', NULL, NULL, 'list', 41.7035, 44.78505, 'ანასტასია ერისთავის შესახვევი', 'exact'),
 (11, 107, NULL, '1;3', 3, '', '', NULL, NULL, 'list', 41.7035, 44.78505, 'ანასტასია ერისთავის შესახვევი', 'exact'),
 (12, 109, NULL, '2', 2, '', '', NULL, NULL, 'int', 41.7062, 44.78205, 'ყაზბეგის ჩიხი', 'exact'),
 (13, 110, NULL, 'office 208', NULL, NULL, NULL, NULL, NULL, 'junk', 41.7062, 44.78305, 'კაკლის ჩიხი', 'exact'),
 (14, 113, NULL, '3', 3, '', '', NULL, NULL, 'int', 41.69002, 44.8062, 'ლესელიძის ქუჩა', 'exact'),
 (15, 113, NULL, '3', 3, '', '', NULL, NULL, 'int', 41.69002, 44.8062, 'ლესელიძის ქუჩა', 'exact'),
 (16, NULL, 14, '12', 12, '', '', NULL, NULL, 'int', 41.7081, 44.7841, NULL, 'place'),
 (17, NULL, NULL, '5', 5, '', '', NULL, NULL, 'int', 41.7000, 44.7700, 'უცნობი ქუჩა', 'none'),
 (18, 112, NULL, '4', 4, '', '', NULL, NULL, 'int', 41.69005, 44.7870, 'ზუბალაშვილის ქუჩა', 'homonym_swap');
