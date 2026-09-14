"""
事業所固有の帳票様式（はぴねす様式）

- AgencyMeetingReport     : 関係機関連携加算Ⅱ 報告書
- SpecializedSupportPlan  : 専門的支援実施計画書（理学療法系）
個別支援計画書（別紙1／詳細版）は support_plans のデータ＋ SupportPlan.form_extra / PlanGoal.form_extra から作る。
"""
from django.db import models

from accounts.models import StaffAccount
from beneficiaries.models import Beneficiary
from facilities.models import Facility


class AgencyMeetingReport(models.Model):
    """関係機関連携加算Ⅱ 報告書（学校・医療機関などとの会議の記録）"""
    FORMAT_CHOICES = [('face', '対面形式'), ('online', 'オンライン'), ('phone', '電話'), ('document', '文書')]

    facility    = models.ForeignKey(Facility, on_delete=models.CASCADE, verbose_name='施設')
    beneficiary = models.ForeignKey(Beneficiary, on_delete=models.CASCADE, related_name='agency_meetings', verbose_name='児童')
    date        = models.DateField(verbose_name='会議開催日')
    start_time  = models.TimeField(null=True, blank=True, verbose_name='開始')
    end_time    = models.TimeField(null=True, blank=True, verbose_name='終了')
    place       = models.CharField(max_length=200, blank=True, verbose_name='会議場所')
    format      = models.CharField(max_length=10, choices=FORMAT_CHOICES, default='face', verbose_name='会議形式')
    participants = models.JSONField(default=list, blank=True, verbose_name='参加者')  # [{"affiliation": "", "name": ""}]
    purpose     = models.TextField(blank=True, verbose_name='会議の目的')
    result      = models.TextField(blank=True, verbose_name='結果・報告内容')
    opinions    = models.TextField(blank=True, verbose_name='関係機関からの意見・助言')
    policy      = models.TextField(blank=True, verbose_name='事業所としての対応方針')
    recorder    = models.ForeignKey(StaffAccount, on_delete=models.SET_NULL, null=True, blank=True, verbose_name='記録者')
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '関係機関連携報告書'
        verbose_name_plural = '関係機関連携報告書'
        ordering = ['-date', '-pk']

    def __str__(self):
        return f'{self.beneficiary} {self.date} 関係機関連携報告書'

    @property
    def time_range(self):
        f = lambda t: t.strftime('%H:%M') if t else ''  # noqa: E731
        return f'{f(self.start_time)}～{f(self.end_time)}' if (self.start_time or self.end_time) else ''

    def participant_rows(self, rows=4):
        """様式は 2 名 × 4 行。足りない分は空欄で埋める"""
        ps = [p for p in (self.participants or []) if p.get('affiliation') or p.get('name')]
        ps += [{'affiliation': '', 'name': ''}] * max(0, rows * 2 - len(ps))
        return [ps[i:i + 2] for i in range(0, rows * 2, 2)]


class SpecializedSupportPlan(models.Model):
    """専門的支援実施計画書（身体機能・基本動作の評価と訓練内容）"""
    ASSIST_CHOICES = [('', '—'), ('independent', '自立'), ('partial', '一部介助'), ('assist', '介助'), ('none', '非実施')]
    ROM_PARTS = ['肩', '肘', '手', '股', '膝', '足']
    WEAK_PARTS = ['頸部', '体幹', '上肢', '下肢']
    TONE_CHOICES = [('', '—'), ('high', '亢進'), ('low', '減弱')]
    YESNO_CHOICES = [('', '—'), ('yes', 'あり'), ('no', 'なし')]
    ABMS_ITEMS = [('neck', '頸部保持'), ('sitting', '座位保持'), ('floor', '平面移動'), ('standing', '立位保持'), ('walking', '歩行')]
    ABMS_T_ITEMS = [('oral', '口腔顔面'), ('hand', '手先'), ('one_leg', '片足'), ('both_legs', '両足'), ('stairs', '階段')]
    SUPPORT_ITEMS = ['関節可動域訓練', 'ストレッチング', '筋力強化訓練', 'バランス訓練', '寝返り訓練', '座位訓練', '立位訓練',
                     '立ち上がり訓練', '立位保持訓練', '歩行訓練', '応用歩行訓練（階段など）', 'リラクゼーション', '呼吸理学療法', 'ポジショニング']
    MOVEMENTS = [('move_rolling', '寝返り'), ('move_sitting', '座位保持'), ('move_getting_up', '起き上がり'),
                 ('move_standing', '立位保持'), ('move_stand_up', '立ち上がり')]

    facility     = models.ForeignKey(Facility, on_delete=models.CASCADE, verbose_name='施設')
    beneficiary  = models.ForeignKey(Beneficiary, on_delete=models.CASCADE, related_name='specialized_plans', verbose_name='利用児')
    period_start = models.DateField(null=True, blank=True, verbose_name='想定支援期間（開始）')
    period_end   = models.DateField(null=True, blank=True, verbose_name='想定支援期間（終了）')
    wishes       = models.TextField(blank=True, verbose_name='本人・ご家族の希望')
    # 身体機能
    rom_parts    = models.JSONField(default=list, blank=True, verbose_name='関節可動域制限（部位）')
    pain_site    = models.CharField(max_length=100, blank=True, verbose_name='疼痛（部位）')
    weak_parts   = models.JSONField(default=list, blank=True, verbose_name='筋力低下（部位）')
    balance      = models.CharField(max_length=5, choices=YESNO_CHOICES, blank=True, verbose_name='バランス障害')
    cardio       = models.CharField(max_length=100, blank=True, verbose_name='呼吸・循環機能障害')
    muscle_tone  = models.CharField(max_length=5, choices=TONE_CHOICES, blank=True, verbose_name='筋緊張異常')
    other_physical = models.CharField(max_length=200, blank=True, verbose_name='他')
    # 基本動作
    move_rolling    = models.CharField(max_length=12, choices=ASSIST_CHOICES, blank=True, verbose_name='寝返り')
    move_sitting    = models.CharField(max_length=12, choices=ASSIST_CHOICES, blank=True, verbose_name='座位保持')
    move_getting_up = models.CharField(max_length=12, choices=ASSIST_CHOICES, blank=True, verbose_name='起き上がり')
    move_standing   = models.CharField(max_length=12, choices=ASSIST_CHOICES, blank=True, verbose_name='立位保持')
    move_stand_up   = models.CharField(max_length=12, choices=ASSIST_CHOICES, blank=True, verbose_name='立ち上がり')
    other_movement  = models.CharField(max_length=200, blank=True, verbose_name='その他')
    abms         = models.JSONField(default=dict, blank=True, verbose_name='ABMS-C')
    abms_t       = models.JSONField(default=dict, blank=True, verbose_name='ABMS-C Type T')
    key_areas    = models.TextField(blank=True, verbose_name='重要領域')
    goals        = models.TextField(blank=True, verbose_name='達成目標')
    support_items = models.JSONField(default=list, blank=True, verbose_name='支援内容')
    support_other = models.CharField(max_length=200, blank=True, verbose_name='支援内容（その他）')
    implementation = models.TextField(blank=True, verbose_name='実施内容')
    explained_date = models.DateField(null=True, blank=True, verbose_name='本人・家族への説明日')
    explained_to   = models.CharField(max_length=100, blank=True, verbose_name='説明を受けた人')
    explained_by   = models.ForeignKey(StaffAccount, on_delete=models.SET_NULL, null=True, blank=True, verbose_name='説明者')
    created_at   = models.DateTimeField(auto_now_add=True)
    updated_at   = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = '専門的支援実施計画書'
        verbose_name_plural = '専門的支援実施計画書'
        ordering = ['-period_start', '-pk']

    def __str__(self):
        return f'{self.beneficiary} 専門的支援実施計画書（{self.period_start or "期間未設定"}）'

    def movement_rows(self):
        return [(label, getattr(self, f'get_{field}_display')() if getattr(self, field) else '') for field, label in self.MOVEMENTS]
