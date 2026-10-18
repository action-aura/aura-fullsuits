import type { DeepStrings } from '../types';
import type { clinicEn } from './clinic.en';

export const clinicAr: DeepStrings<typeof clinicEn> = {
  hero: {
    status: 'متاح الآن', // claims: clinic-claims.md (page chrome, no claim) // AR: drafted, native check pending
    title: 'يوم عيادتك في مكان واحد', // claims: clinic-claims.md Patient file // AR: drafted, native check pending
    accent: 'مكان واحد', // claims: clinic-claims.md Patient file // AR: drafted, native check pending
    lead: 'ملفات المرضى والمواعيد ومخطط الأسنان والحسابات على جهاز العيادة نفسه. يعمل بدون إنترنت.', // claims: clinic-claims.md Works offline // AR: drafted, native check pending
  },
  rail: {
    title: 'يوم في العيادة', // claims: clinic-claims.md (page chrome, no claim) // AR: drafted, native check pending
    moments: [
      {
        title: 'احجز الموعد', // claims: clinic-claims.md Appointment calendar // AR: drafted, native check pending
        line: 'تقويم مواعيد بحالات واضحة وعطلات، وجدولة لكل طبيب.', // claims: clinic-claims.md Appointment calendar, Per-dentist scheduling
      },
      {
        title: 'افتح ملف المريض', // claims: clinic-claims.md Patient file // AR: drafted, native check pending
        line: 'بيانات شخصية وتاريخ طبي وحفظ صور الأشعة والصور السريرية.', // claims: clinic-claims.md Patient file
      },
      {
        title: 'ارسم مخطط الأسنان وضع الخطة العلاجية', // claims: clinic-claims.md Tooth chart, Treatment plans // AR: drafted, native check pending
        line: 'مخطط الأسنان مع حالات الأسنان وكتالوج قابل للتعديل. خطط علاجية بالأهداف والتكلفة التقديرية والتواريخ.', // claims: clinic-claims.md Tooth chart, Treatment plans
      },
      {
        title: 'ورقة المتابعة والدفعة', // claims: clinic-claims.md Follow-up sheet // AR: drafted, native check pending
        line: 'لكل زيارة: الإجراء ورقم السن والسعر والخصم وتكلفة المختبر والدفعة وربح العيادة.', // claims: clinic-claims.md Follow-up sheet
      },
      {
        title: 'التقارير', // claims: clinic-claims.md Reports // AR: drafted, native check pending
        line: 'تقارير أسبوعية وشهرية ولفترة مخصصة، ولوحة مؤشرات، وتقارير لكل طبيب.', // claims: clinic-claims.md Reports
      },
    ],
  },
  features: {
    title: 'ماذا بداخله', // claims: clinic-claims.md (page chrome, no claim) // AR: drafted, native check pending
    items: [
      {
        title: 'سجلات المرضى', // claims: clinic-claims.md Duplicate-patient warning // AR: drafted, native check pending
        line: 'تنبيه تكرار المريض، دمج المكررين، استيراد مرضى من ملف CSV.', // claims: clinic-claims.md Duplicate-patient warning
      },
      {
        title: 'جدولة لكل طبيب', // claims: clinic-claims.md Per-dentist scheduling // AR: drafted, native check pending
        line: 'يرى الطبيب مواعيده وعلاجاته وفواتيره فقط عند التفعيل.', // claims: clinic-claims.md Per-dentist scheduling
      },
      {
        title: 'حساب واحد لكل مريض', // claims: clinic-claims.md Account ledger // AR: drafted, native check pending
        line: 'حساب مالي موحّد لكل مريض مع سجل الدفعات، وتقرير الذمم.', // claims: clinic-claims.md Account ledger, Receivables report
      },
      {
        title: 'الفواتير وكشوف الحساب', // claims: clinic-claims.md Printed invoices // AR: drafted, native check pending
        line: 'فواتير وكشوف حساب مطبوعة بالعربية والإنجليزية.', // claims: clinic-claims.md Printed invoices
      },
      {
        title: 'مخزن المواد', // claims: clinic-claims.md Materials stock // AR: drafted, native check pending
        line: 'حركة المخزون وتتبّع الانتهاء واستهلاك المواد لكل إجراء. على الحاسوب فقط.', // claims: clinic-claims.md Materials stock // AR: drafted, native check pending
      },
      {
        title: 'تطبيق أندرويد', // claims: clinic-claims.md Android phone app // AR: drafted, native check pending
        line: 'المرضى والمواعيد والزيارات والفواتير والتقارير ومخطط الأسنان والخطط، على هاتفك.', // claims: clinic-claims.md Android phone app // AR: drafted, native check pending
      },
    ],
  },
  works: {
    title: 'الهاتف والحاسوب متزامنان', // claims: clinic-claims.md Sync // AR: drafted, native check pending
    line: 'مزامنة الهاتف مع العيادة عبر الشبكة المحلية أو السحابة أو بلوتوث. خيار السحابة يحتاج إلى الإنترنت.', // claims: clinic-claims.md Sync // AR: drafted, native check pending
  },
  privacy: {
    title: 'خصوصية وثقة', // claims: clinic-claims.md Encrypted database // AR: drafted, native check pending
    items: [
      'قاعدة بيانات المرضى مشفّرة على جهاز العيادة.', // claims: clinic-claims.md Encrypted database
      'أدوار (مدير، طبيب، موظف) وصلاحيات تفصيلية لكل مستخدم.', // claims: clinic-claims.md Roles
      'يعمل بدون إنترنت. نسخ احتياطي بضغطة ونسخ تلقائي.', // claims: clinic-claims.md Works offline, Backup // AR: drafted, native check pending
    ],
  },
  run: {
    title: 'ما الذي تحتاجه للتشغيل', // claims: clinic-claims.md (page chrome, no claim) // AR: drafted, native check pending
    line: 'يعمل Aura Clinic على جهاز ويندوز في العيادة. أضف تطبيق أندرويد للمزامنة معه.', // claims: clinic-claims.md Encrypted database, Android phone app // AR: drafted, native check pending
  },
  cta: {
    title: 'احجز عرضًا مجانيًا', // claims: clinic-claims.md (page chrome, no claim)
    lead: 'حدثنا عن عيادتك وسنعرض عليك Aura Clinic.', // claims: clinic-claims.md (page chrome, no claim) // AR: drafted, native check pending
  },
};
