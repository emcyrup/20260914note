"""
標準の加算マスタ（全事業所共通・法令ベース）と、それを DB に合わせ込む処理。

facilities/views.py の「標準加算マスタを読み込む」と、migration（facilities 0031）の両方から使う。
migration から使うときは AddonMaster に apps.get_model('facilities', 'AddonMaster') を渡す。
"""
# 標準加算マスタの初期データ（法令ベース）
DEFAULT_ADDONS = [
    # 単位数とサービスコードは、令和6年4月施行の「放課後等デイサービスコード表」（サービス種類コード 63）と、
    # こども家庭庁「令和６年度障害福祉サービス等報酬改定（障害児支援関係）改定事項の概要」（令和6年4月1日）による。
    # 児童発達支援だけの加算（食事提供加算・人工内耳Ⅰ）と共生型の加算は、サービス種類コードが違うのでコードを入れていない。
    # 定員区分で単位数が変わる体制加算は 0 にして、説明に区分ごとの値を書いてある（事業所ごとに加算設定で入れる）。
    # ---- 個別加算（利用者×日／回）----
    {'name': '送迎加算（往・迎え）',               'addon_type': 'individual', 'code': '636240', 'unit_count': 54,  'description': '利用者を自宅等から事業所まで迎えに行った場合（片道）。'},
    {'name': '送迎加算（復・送り）',               'addon_type': 'individual', 'code': '636240', 'unit_count': 54,  'description': '利用者を事業所から自宅等まで送り届けた場合（片道）。'},
    {'name': '送迎加算（同一敷地内・片道）',       'addon_type': 'individual', 'code': '636243', 'unit_count': 38,  'description': '事業所と同一敷地内等の送迎（54 の 70％）。'},
    {'name': '送迎加算（重症心身障害児・片道の上乗せ）',            'addon_type': 'individual', 'code': '636242', 'unit_count': 40,  'description': '重症心身障害児を職員が付き添って送迎した場合、片道の 54 に加えて算定（コード表の「一定条件１」）。同一敷地内は 28。'},
    {'name': '送迎加算（医療的ケア児 スコア16点以上・片道の上乗せ）', 'addon_type': 'individual', 'code': '636245', 'unit_count': 80,  'description': '医療的ケアスコア16点以上の児を、医療的ケアが可能な職員が付き添って送迎した場合の上乗せ（コード表の「一定条件２」）。同一敷地内は 56。'},
    {'name': '送迎加算（医療的ケア児 その他・片道の上乗せ）',        'addon_type': 'individual', 'code': '636242', 'unit_count': 40,  'description': '上記以外の医療的ケア児を、医療的ケアが可能な職員が付き添って送迎した場合の上乗せ（コード表の「一定条件１」）。'},
    {'name': '延長支援加算（30分以上1時間未満）',  'addon_type': 'individual', 'code': '636301', 'unit_count': 61,  'description': '基本報酬の最長の時間区分（放デイは平日3時間・学校休業日5時間）を超えて、預かりニーズに対応した支援を計画的に行った場合。この区分は、利用者の都合等で延長時間が計画より短くなったときだけ算定できる。職員2名以上（うち1名は人員基準上の職員）。'},
    {'name': '延長支援加算（1時間以上2時間未満）', 'addon_type': 'individual', 'code': '636302', 'unit_count': 92,  'description': '延長時間が1時間以上2時間未満。'},
    {'name': '延長支援加算（2時間以上）',          'addon_type': 'individual', 'code': '636303', 'unit_count': 123, 'description': '延長時間が2時間以上。'},
    {'name': '延長支援加算（重症児・医療的ケア児 30分以上1時間未満）', 'addon_type': 'individual', 'code': '636304', 'unit_count': 128, 'description': '重症心身障害児・医療的ケア児の延長支援。30分以上1時間未満（計画より短くなったときのみ）。'},
    {'name': '延長支援加算（重症児・医療的ケア児 1時間以上2時間未満）', 'addon_type': 'individual', 'code': '636305', 'unit_count': 192, 'description': '重症心身障害児・医療的ケア児の延長支援。1時間以上2時間未満。'},
    {'name': '延長支援加算（重症児・医療的ケア児 2時間以上）',        'addon_type': 'individual', 'code': '636306', 'unit_count': 256, 'description': '重症心身障害児・医療的ケア児の延長支援。2時間以上。'},
    {'name': '欠席時対応加算',                     'addon_type': 'individual', 'code': '635495', 'unit_count': 94,  'description': '利用者が急病等で欠席した際に連絡・相談援助を行った場合。月4回まで（重症心身障害児を支援する場合で定員充足率80％未満のときは月8回まで）。令和6年度に「欠席時対応加算Ⅱ」（30分以内の提供）は廃止。'},
    {'name': '家族支援加算Ⅰ（居宅訪問・1時間以上）', 'addon_type': 'individual', 'code': '635603', 'unit_count': 300, 'description': '家族（きょうだいを含む）の居宅を訪問して、個別に1時間以上の相談援助等を行った場合。Ⅰは月4回まで。'},
    {'name': '家族支援加算Ⅰ（居宅訪問・1時間未満）', 'addon_type': 'individual', 'code': '635604', 'unit_count': 200, 'description': '家族の居宅を訪問して、個別に1時間未満の相談援助等を行った場合。Ⅰは月4回まで。'},
    {'name': '家族支援加算Ⅰ（事業所等で対面）',      'addon_type': 'individual', 'code': '635605', 'unit_count': 100, 'description': '事業所等で家族と対面して、個別に相談援助等を行った場合。Ⅰは月4回まで。'},
    {'name': '家族支援加算Ⅰ（オンライン）',          'addon_type': 'individual', 'code': '635606', 'unit_count': 80,  'description': 'オンラインで家族に個別の相談援助等を行った場合。Ⅰは月4回まで。'},
    {'name': '家族支援加算Ⅱ（グループ・事業所等で対面）', 'addon_type': 'individual', 'code': '635607', 'unit_count': 80,  'description': '複数の家族に対するグループでの相談援助等を事業所等で対面で行った場合。Ⅱは月4回まで。実施記録と参加者名簿を残す。'},
    {'name': '家族支援加算Ⅱ（グループ・オンライン）',   'addon_type': 'individual', 'code': '635608', 'unit_count': 60,  'description': '複数の家族に対するグループでの相談援助等をオンラインで行った場合。Ⅱは月4回まで。実施記録と参加者名簿を残す。'},
    {'name': '子育てサポート加算',                 'addon_type': 'individual', 'code': '635701', 'unit_count': 80,  'description': '保護者に支援場面の観察や参加の機会を提供したうえで、こどもの特性や関わり方について相談援助等を行った場合。月4回まで。'},
    {'name': '専門的支援実施加算',                 'addon_type': 'individual', 'code': '635702', 'unit_count': 150, 'description': '理学療法士等の専門職が、個別・集中的な専門的支援を計画的に行った場合（1回につき）。放デイは月2回〜最大月6回まで（利用日数等による）。専門的支援体制加算と併算定可。日誌に担当者・開始／終了時刻を記録する。'},
    {'name': '関係機関連携加算Ⅰ（計画作成時の会議）',   'addon_type': 'individual', 'code': '636760', 'unit_count': 250, 'description': '保育所や学校等との個別支援計画に関する会議を開催し、連携して個別支援計画を作成等した場合。月1回まで。'},
    {'name': '関係機関連携加算Ⅱ（保育所・学校等との情報連携）', 'addon_type': 'individual', 'code': '636761', 'unit_count': 200, 'description': '保育所や学校等との会議等により情報連携を行った場合。月1回まで。'},
    {'name': '関係機関連携加算Ⅲ（児童相談所・医療機関等との情報連携）', 'addon_type': 'individual', 'code': '636767', 'unit_count': 150, 'description': '児童相談所、医療機関等との会議等により情報連携を行った場合。月1回まで。'},
    {'name': '関係機関連携加算Ⅳ（就学先・就職先との連絡調整）',   'addon_type': 'individual', 'code': '636768', 'unit_count': 200, 'description': '就学先の小学校や就職先の企業等との連絡調整を行った場合。1回限り。'},
    {'name': '事業所間連携加算Ⅰ（中核となる事業所）',   'addon_type': 'individual', 'code': '635710', 'unit_count': 500, 'description': 'セルフプランで複数事業所を併用する児について、コーディネートの中核となる事業所として会議を開催する等により事業所間の情報連携を行い、家族への助言援助や自治体との情報連携等を行った場合。月1回まで。'},
    {'name': '事業所間連携加算Ⅱ（会議に参画）',        'addon_type': 'individual', 'code': '635711', 'unit_count': 150, 'description': 'セルフプランで複数事業所を併用する児について、Ⅰの会議に参画する等により事業所間の情報連携を行い、事業所内で共有して支援に反映させた場合。月1回まで。'},
    {'name': '保育・教育等移行支援加算（退所前の取組）',   'addon_type': 'individual', 'code': '636801', 'unit_count': 500, 'description': '退所前に、移行先への助言援助や関係機関等との移行に向けた協議等を行った場合。2回まで（コード表の「入所中」）。'},
    {'name': '保育・教育等移行支援加算（退所後の居宅訪問）', 'addon_type': 'individual', 'code': '636802', 'unit_count': 500, 'description': '退所後に居宅等を訪問して相談援助を行った場合。1回まで（コード表の「退所後」）。'},
    {'name': '保育・教育等移行支援加算（退所後の保育所等訪問）', 'addon_type': 'individual', 'code': '636802', 'unit_count': 500, 'description': '退所後に保育所等を訪問して助言・援助を行った場合。1回まで（コード表の「退所後」）。'},
    {'name': '通所自立支援加算',                   'addon_type': 'individual', 'code': '635709', 'unit_count': 60,  'description': '放デイ。学校・居宅等と事業所の間の移動について、自立して通所できるよう職員が付き添って計画的に支援した場合（1回につき）。算定開始から3か月まで。'},
    {'name': '自立サポート加算',                   'addon_type': 'individual', 'code': '635708', 'unit_count': 100, 'description': '放デイ。高校2・3年生について、卒業後の生活に向けて学校や企業等と連携しながら相談援助や体験等の支援を計画的に行った場合（1回につき）。月2回まで。'},
    {'name': '入浴支援加算',                       'addon_type': 'individual', 'code': '635088', 'unit_count': 70,  'description': '医療的ケア児または重症心身障害児に、発達支援とあわせて入浴支援を行った場合。放デイ 70、児童発達支援 55。月8回まで。'},
    {'name': '個別サポート加算Ⅰ（ケアニーズの高い児）',   'addon_type': 'individual', 'code': '636762', 'unit_count': 90,  'description': '放デイ。ケアニーズの高い障害児に支援を行った場合（主として重症心身障害児が利用する事業所の基本報酬を算定している場合を除く）。'},
    {'name': '個別サポート加算Ⅰ（ケアニーズ＋基礎研修修了者を配置）', 'addon_type': 'individual', 'code': '636775', 'unit_count': 120, 'description': '放デイ。ケアニーズの高い障害児に、強度行動障害支援者養成研修（基礎研修）修了者を配置して支援を行った場合。強度行動障害児支援加算との併算定不可。'},
    {'name': '個別サポート加算Ⅰ（著しく重度）',          'addon_type': 'individual', 'code': '636776', 'unit_count': 120, 'description': '放デイ。著しく重度の障害児に支援を行った場合（主として重症心身障害児が利用する事業所の基本報酬を算定している場合を除く）。児童発達支援はこの区分だけで 120。'},
    {'name': '個別サポート加算Ⅱ（要保護・要支援児童）',   'addon_type': 'individual', 'code': '636763', 'unit_count': 150, 'description': '要保護児童・要支援児童に対し、児童相談所やこども家庭センター等と連携（支援の状況等を6か月に1回以上共有）して支援を行った場合。'},
    {'name': '個別サポート加算Ⅲ（不登校）',            'addon_type': 'individual', 'code': '635706', 'unit_count': 70,  'description': '放デイ。不登校の状態にある障害児に対して、学校との連携のもと、家族への相談援助等を含めて支援を行った場合。'},
    {'name': '強度行動障害児支援加算Ⅰ（児基準20点以上）', 'addon_type': 'individual', 'code': '636225', 'unit_count': 200, 'description': '強度行動障害支援者養成研修（実践研修）修了者を配置し、児基準20点以上の児に支援計画シートを作成して支援を行った場合。'},
    {'name': '強度行動障害児支援加算Ⅱ（児基準30点以上）', 'addon_type': 'individual', 'code': '635611', 'unit_count': 250, 'description': '中核的人材養成研修修了者を配置し、児基準30点以上の児に支援計画シートを作成して支援を行った場合。'},
    {'name': '強度行動障害児支援加算（開始から90日以内の上乗せ）', 'addon_type': 'individual', 'code': '635612', 'unit_count': 500, 'description': '強度行動障害児支援加算Ⅰ・Ⅱの算定開始から90日以内の期間に、さらに加算。'},
    {'name': '集中的支援加算',                     'addon_type': 'individual', 'code': '635041', 'unit_count': 1000, 'description': '強度行動障害を有する児の状態が悪化した場合に、広域的支援人材が訪問して集中的な支援を行った場合。3か月以内・月4回まで。'},
    {'name': '人工内耳装用児支援加算',             'addon_type': 'individual', 'code': '635501', 'unit_count': 150, 'description': '眼科・耳鼻咽喉科の医療機関との連携のもと言語聴覚士を配置し、人工内耳を装用している児に専門的な支援を計画的に行った場合。聴力検査室のある児童発達支援センターのⅠ（445〜603）は別のサービスコード。'},
    {'name': '視覚・聴覚・言語機能障害児支援加算', 'addon_type': 'individual', 'code': '635705', 'unit_count': 100, 'description': '視覚・聴覚・言語機能に重度の障害のある児に対して、意思疎通に専門性のある人材を配置して支援を行った場合。'},
    {'name': '共生型サービス医療的ケア児支援加算', 'addon_type': 'individual', 'unit_count': 400, 'description': '共生型サービス事業所が看護職員等を配置し、地域貢献活動を行っているものとして届け出たうえで、医療的ケア児に支援を行った場合。医療連携体制加算Ⅰ〜Ⅶとの併算定不可。サービスコードは共生型のコード表による。'},
    {'name': '食事提供加算Ⅰ（栄養士の助言）',     'addon_type': 'individual', 'unit_count': 30,  'description': '児童発達支援センター。低所得・中間所得世帯の児に、栄養士の助言・指導のもとで栄養面等に配慮した食事を提供した場合。放デイにはこの加算は無い。'},
    {'name': '食事提供加算Ⅱ（管理栄養士の助言）', 'addon_type': 'individual', 'unit_count': 40,  'description': '児童発達支援センター。管理栄養士等の助言・指導のもとで食事を提供した場合。放デイにはこの加算は無い。'},
    # 医療連携体制加算（Ⅰ〜Ⅵは改定前と同じ、Ⅶは令和6年度に 100→250）
    {'name': '医療連携体制加算Ⅰ（看護1時間未満）',       'addon_type': 'individual', 'code': '636230', 'unit_count': 32,   'description': '医療的ケアを必要としない利用者への看護で、提供時間が1時間未満。'},
    {'name': '医療連携体制加算Ⅱ（看護1〜2時間未満）',     'addon_type': 'individual', 'code': '636231', 'unit_count': 63,   'description': '医療的ケアを必要としない利用者への看護で、提供時間が1時間以上2時間未満。'},
    {'name': '医療連携体制加算Ⅲ（看護2時間以上）',        'addon_type': 'individual', 'code': '636232', 'unit_count': 125,  'description': '医療的ケアを必要としない利用者への看護で、提供時間が2時間以上。'},
    {'name': '医療連携体制加算Ⅳ（医療的ケア4時間未満・1人）',      'addon_type': 'individual', 'code': '636280', 'unit_count': 800,  'description': '医療的ケアを必要とする利用者への看護（4時間未満）。利用者が1人のとき。'},
    {'name': '医療連携体制加算Ⅳ（医療的ケア4時間未満・2人）',      'addon_type': 'individual', 'code': '636281', 'unit_count': 500,  'description': '医療的ケアを必要とする利用者への看護（4時間未満）。利用者が2人のとき。'},
    {'name': '医療連携体制加算Ⅳ（医療的ケア4時間未満・3〜8人）',   'addon_type': 'individual', 'code': '636282', 'unit_count': 400,  'description': '医療的ケアを必要とする利用者への看護（4時間未満）。利用者が3人以上8人以下のとき。'},
    {'name': '医療連携体制加算Ⅴ（医療的ケア4時間以上・1人）',      'addon_type': 'individual', 'code': '636283', 'unit_count': 1600, 'description': '医療的ケアを必要とする利用者への看護（4時間以上）。利用者が1人のとき。'},
    {'name': '医療連携体制加算Ⅴ（医療的ケア4時間以上・2人）',      'addon_type': 'individual', 'code': '636284', 'unit_count': 960,  'description': '医療的ケアを必要とする利用者への看護（4時間以上）。利用者が2人のとき。'},
    {'name': '医療連携体制加算Ⅴ（医療的ケア4時間以上・3〜8人）',   'addon_type': 'individual', 'code': '636285', 'unit_count': 800,  'description': '医療的ケアを必要とする利用者への看護（4時間以上）。利用者が3人以上8人以下のとき。'},
    {'name': '医療連携体制加算Ⅵ（喀痰吸引等の指導）',      'addon_type': 'individual', 'code': '639992', 'unit_count': 500,  'description': '看護職員が介護職員等に喀痰吸引等の指導を行った場合。利用者数で按分した単位数を算定する。'},
    {'name': '医療連携体制加算Ⅶ（喀痰吸引等の実施）',      'addon_type': 'individual', 'code': '636233', 'unit_count': 250,  'description': '喀痰吸引等が必要な障害児に対して、認定特定行為業務従事者が医療機関等との連携により喀痰吸引等を行った場合（医療的ケア区分による基本報酬を算定している場合は算定しない）。令和6年度に 100→250。'},
    # ---- 体制加算（施設全体・日／月）。定員区分で単位数が変わるものは 0 にして、事業所ごとに加算設定で入れる ----
    {'name': '児童指導員等加配加算',               'addon_type': 'facility', 'unit_count': 0,   'description': '基準の人員に加えて児童指導員等またはその他の従業者を配置。放デイ・児童発達支援事業所の1日あたり（定員10人以下／11〜20人／21人以上）：常勤専従・経験5年以上 187／125／75（コード 634400〜）、常勤専従・経験5年未満 152／101／59、常勤換算・経験5年以上 123／82／49、常勤換算・経験5年未満 107／71／43、その他の従業者 90／60／36。'},
    {'name': '専門的支援体制加算',                 'addon_type': 'facility', 'unit_count': 0,   'description': '基準の人員に加えて理学療法士等を配置している場合。放デイ・児童発達支援事業所の1日あたり、定員10人以下 123／11〜20人 82／21人以上 49（コード 634551〜634553）。'},
    {'name': '福祉専門職員配置等加算Ⅰ',           'addon_type': 'facility', 'code': '635492', 'unit_count': 15,  'description': '社会福祉士等の有資格者を一定割合以上配置している場合（1日につき）。'},
    {'name': '福祉専門職員配置等加算Ⅱ',           'addon_type': 'facility', 'code': '635490', 'unit_count': 10,  'description': '社会福祉士等の有資格者を一定割合配置している場合（Ⅰより基準が低い）。'},
    {'name': '福祉専門職員配置等加算Ⅲ',           'addon_type': 'facility', 'code': '635491', 'unit_count': 6,   'description': '常勤の従業者が一定割合以上、または勤続年数3年以上の常勤の従業者が一定割合以上の場合。'},
    {'name': '看護職員加配加算',                   'addon_type': 'facility', 'unit_count': 0,   'description': '主として重症心身障害児を支援する事業所が、看護職員を基準を超えて配置している場合。定員と区分で単位数が変わる（重症心身障害児のコード表による）。'},
    {'name': '中核機能強化事業所加算',             'addon_type': 'facility', 'unit_count': 0,   'description': '市町村が地域の中核拠点として位置付ける事業所で、専門人材を配置して関係機関との連携体制を確保しながら専門的・包括的な支援に取り組んだ場合。1日あたり、定員10人以下 187／11〜20人 125／21人以上 75（コード 635801〜635803）。主として重症心身障害児を通わせる事業所は 125〜374。'},
    {'name': '福祉・介護職員等処遇改善加算',       'addon_type': 'facility', 'unit_count': 0,   'description': '職員の処遇改善のための体制加算。単位数ではなく、所定単位数に区分ごとの率を掛ける。サービスコードは区分ごと（635621〜）。'},
    {'name': '利用者負担上限額管理加算',           'addon_type': 'facility', 'code': '635370', 'unit_count': 150, 'description': '複数事業所を利用する利用者の負担上限額を管理する事業所に算定。月1回。'},
]


ADDON_RENAMES = {
    '専門的支援実施加算（個別実施分）': '専門的支援実施加算',
    '関係機関連携加算Ⅰ（計画作成時の会議等）': '関係機関連携加算Ⅰ（計画作成時の会議）',
    '関係機関連携加算Ⅱ（情報連携）': '関係機関連携加算Ⅱ（保育所・学校等との情報連携）',
    '関係機関連携加算Ⅲ（就学・就職時）': '関係機関連携加算Ⅳ（就学先・就職先との連絡調整）',
    '関係機関連携加算Ⅳ（医療機関等）': '関係機関連携加算Ⅲ（児童相談所・医療機関等との情報連携）',
    '家族支援加算（居宅訪問・1時間以上）': '家族支援加算Ⅰ（居宅訪問・1時間以上）',
    '家族支援加算（居宅訪問・1時間未満）': '家族支援加算Ⅰ（居宅訪問・1時間未満）',
    '家族支援加算（事業所で対面）': '家族支援加算Ⅰ（事業所等で対面）',
    '家族支援加算（オンライン）': '家族支援加算Ⅰ（オンライン）',
    '家族支援加算Ⅱ（グループ・事業所で対面）': '家族支援加算Ⅱ（グループ・事業所等で対面）',
    '個別サポート加算Ⅰ': '個別サポート加算Ⅰ（ケアニーズの高い児）',
    '個別サポート加算Ⅰ（基礎研修修了者を配置・著しく重度）': '個別サポート加算Ⅰ（ケアニーズ＋基礎研修修了者を配置）',
    '個別サポート加算Ⅱ': '個別サポート加算Ⅱ（要保護・要支援児童）',
    '個別サポート加算Ⅲ': '個別サポート加算Ⅲ（不登校）',
    '強度行動障害児支援加算': '強度行動障害児支援加算Ⅰ（児基準20点以上）',
    '人工内耳装用児支援加算Ⅱ': '人工内耳装用児支援加算',
    '保育・教育等移行支援加算': '保育・教育等移行支援加算（退所前の取組）',
    '食事提供加算': '食事提供加算Ⅰ（栄養士の助言）',
    '送迎加算（重症心身障害児・往）': '送迎加算（重症心身障害児・片道の上乗せ）',
    '送迎加算（医療的ケア児等の個別送迎）': '送迎加算（医療的ケア児 その他・片道の上乗せ）',
    '中核機能強化加算': '中核機能強化事業所加算',
}
# 令和6年度改定で無くなった名前（同じ内容の加算が標準にあるものは上の付け替えで対応。ここは残っていても無効にするだけ）
ADDON_RETIRED = [
    '医療連携体制加算',                 # Ⅰ〜Ⅶに分かれた
    '送迎加算（重症心身障害児・復）',   # 往復ではなく「片道の上乗せ」1行にまとめた
    '初期加算',                         # 令和6年度の放デイ・児発のコード表に無い（障害児入所施設の加算）
    '自立支援担当職員配置加算',         # 令和6年度は「自立サポート加算」（個別加算）に替わった
]


def load_default_addons(AddonMaster=None):
    """
    標準の加算マスタを DB に合わせ込む。戻り値は (追加, 付け替え, 更新, 無効化) の件数。
    - 名前が無いものは追加する
    - 名前を変えた加算は付け替える（請求に使われている行はそのまま名前と単位数だけ変わる）
    - 標準にある名前で単位数・説明が違うものは標準の値にする（事業所ごとの上書きは FacilityAddonSetting にあるので影響しない）
    - サービスコードは、空のときだけ標準の値を入れる
    - 令和6年度改定で無くなった名前は無効にする
    """
    if AddonMaster is None:
        from .models import AddonMaster
    defaults = {item['name']: item for item in DEFAULT_ADDONS}
    renamed = 0
    for old, new in ADDON_RENAMES.items():
        row = AddonMaster.objects.filter(name=old).first()
        if row is None or AddonMaster.objects.filter(name=new).exists():
            continue
        item = defaults[new]
        row.name, row.unit_count, row.description = new, item['unit_count'], item['description']
        row.addon_type = item['addon_type']
        row.code = row.code or item.get('code', '')
        row.save(update_fields=['name', 'unit_count', 'description', 'addon_type', 'code'])
        renamed += 1
    added = updated = 0
    for item in DEFAULT_ADDONS:
        row = AddonMaster.objects.filter(name=item['name']).first()
        if row is None:
            AddonMaster.objects.create(**item)
            added += 1
        else:
            changed = (row.unit_count, row.description, row.addon_type) != (item['unit_count'], item['description'], item['addon_type'])
            row.unit_count, row.description, row.addon_type = item['unit_count'], item['description'], item['addon_type']
            # サービスコードは、空のときだけ標準の値を入れる（開発向けユーザーが入れた値は消さない）
            if not row.code and item.get('code'):
                row.code = item['code']
                changed = True
            if changed:
                row.save(update_fields=['unit_count', 'description', 'addon_type', 'code'])
                updated += 1
    retired = AddonMaster.objects.filter(name__in=ADDON_RETIRED, is_active=True).update(is_active=False)
    return added, renamed, updated, retired
