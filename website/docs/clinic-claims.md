# Aura Clinic: claims evidence pack (2026-10-18)

Source: a read-only sweep of the DentaCare / Aura Clinic codebase (`C:\Users\MSI\Desktop\clinic`), plus the owner-checked company profile (`content.py`).

**Key to the paths**

| Prefix | File |
|---|---|
| D | `dental_clinic.py` (routes) |
| T | `templates.py` (desktop UI) |
| M | `clinic_mobile_app/lib` |
| P | `content.py` |

The product name is "Aura Clinic" (CHANGELOG 2026-10-18).

**Rule:** the website may only use claims marked SHIPPED. Use the wording given here; it is narrower than what you might guess.

## Safe claims

| Claim (EN) | Claim (AR draft) | Evidence | Status |
|---|---|---|---|
| Patient file with personal details, medical history, and X-ray / clinical photo storage | ملف المريض: بيانات شخصية وتاريخ طبي وحفظ صور الأشعة والصور السريرية | D:4270, D:4394, D:7112; T:9266, T:9334; M patient_detail_screen | SHIPPED, AR |
| Duplicate-patient warning, merge, CSV patient import | تنبيه تكرار المريض، دمج المكررين، استيراد مرضى من ملف CSV | D:4352, D:6920, D:6935, D:7001, D:7052 | SHIPPED, AR |
| Appointment calendar with statuses and holidays | تقويم مواعيد بحالات واضحة وعطلات | D:5268, D:5423, D:6476; T:2700, T:2715 | SHIPPED, AR |
| Per-dentist scheduling; a dentist sees only their own appointments, treatments and invoices (when enabled) | جدولة لكل طبيب، ويرى الطبيب مواعيده وعلاجاته وفواتيره فقط عند التفعيل | CHANGELOG 2026-07-18 `dentist_scope()`; D:3819 | SHIPPED, AR. NOT "own patients": patient records are not scoped |
| Follow-up sheet per visit: procedure, tooth number, price, discount, lab cost, payment, clinic profit | ورقة المتابعة: الإجراء ورقم السن والسعر والخصم وتكلفة المختبر وصافي الربح | D:4694, D:4913; T:2803; M followup.dart | SHIPPED, AR |
| Tooth chart (odontogram) with conditions and a customizable catalog | مخطط الأسنان مع حالات الأسنان وكتالوج قابل للتعديل | D:5561, D:4474; T:9164, T:4969; M odontogram_view | SHIPPED, AR |
| Treatment plans: name, goals, estimated cost, dates | الخطط العلاجية: الاسم والأهداف والتكلفة التقديرية والتواريخ | D:5485, D:5531; T:9504 | SHIPPED (basic), AR |
| One account ledger per patient with payment history | حساب مالي موحّد لكل مريض مع سجل الدفعات | D:7460, D:6361, D:6259; M billing_accounts_view | SHIPPED, AR |
| Printed invoices and patient statements in Arabic and English | فواتير وكشوف حساب مطبوعة بالعربية والإنجليزية | D:7747 (lang=ar, rtl); T:3299; M patient_statement_pdf | SHIPPED, AR |
| Receivables report | تقرير الذمم | D:6173 | SHIPPED, AR |
| Optional sales tax on new bills (off by default) | ضريبة مبيعات اختيارية على الفواتير الجديدة | T:3530; clinic_tax.py | SHIPPED (setting) |
| Expenses and profit | تتبّع المصروفات والربح | D:5736 | SHIPPED, AR |
| Weekly, monthly and custom-range reports, dashboard, per-dentist reporting | تقارير أسبوعية وشهرية ولفترة مخصصة ولوحة مؤشرات وتقارير لكل طبيب | D:5812, D:5984, D:4211; T:2981-2990 | SHIPPED, AR. No export claim |
| Materials stock: restock, adjust, write-off, low stock, expiry tracking, materials per procedure | مخزن المواد: حركة المخزون وتتبّع الانتهاء واستهلاك المواد لكل إجراء | D:5073-5234; T:2923, T:2974 | SHIPPED (desktop only) |
| Roles (admin, dentist, staff) with granular per-user permissions | أدوار (مدير، طبيب، موظف) وصلاحيات تفصيلية لكل مستخدم | permissions.py; D:3833-4036 | SHIPPED, AR |
| Login lockout after repeated wrong passwords, audit log | قفل الحساب بعد محاولات خاطئة متكررة، وسجل تدقيق | CHANGELOG 2026-07-18; D:6456 | SHIPPED |
| Patient database encrypted on the clinic's Windows PC | قاعدة بيانات المرضى مشفّرة على جهاز العيادة | encryption_key.py; D:648-670 | SHIPPED on-premise Windows only (not cloud mode) |
| Works offline | يعمل بدون إنترنت | P:141 | SHIPPED |
| Android phone app: patients, appointments, visits, billing, reports, tooth chart, plans | تطبيق أندرويد للمرضى والمواعيد والزيارات والفواتير والتقارير | M screens/*; D:4189; P:130 | SHIPPED (Android) |
| Phone and desktop sync over the local network, the cloud or Bluetooth | مزامنة الهاتف مع العيادة عبر الشبكة المحلية أو السحابة أو بلوتوث | D:7903, D:7992, D:8683-8809, D:8867 | SHIPPED (cloud needs internet; Bluetooth is Windows-side) |
| Post Studio: branded social posts from case photos, before/after labels | استوديو المنشورات: تجهيز صور الحالات للتسويق بقوالب وعلامة العيادة | D:7336, D:7365; T:2543, T:3584 | SHIPPED (editor on desktop; phone can view and share) |
| One-click backup, automatic backups, export and merge, cloud backup once paired | نسخ احتياطي بضغطة ونسخ تلقائي وتصدير ودمج، ونسخ سحابي بعد الربط | D:6524, D:10021, D:6567-6813; T:2455 | SHIPPED (no interval number) |
| Arabic and English interface with full RTL | واجهة عربية وإنجليزية مع دعم كامل للكتابة من اليمين لليسار | T data-i18n; html[dir=rtl] | SHIPPED |

## Do not claim

- JoFotara / e-invoicing submission. Invoices are only queued, and no submission worker is wired.
- WhatsApp reminders, or SMS as a ready feature. SMS needs the clinic's own Twilio account and is off by default. Email reminders also need the clinic's own setup.
- Online booking by patients, or a patient portal.
- An iOS app, or a Mac/Linux desktop app.
- Encryption in cloud mode.
- Insurance claims, DICOM tools, plan approval or e-signature, a payment gateway, or online card payment.
- Compliance certifications (HIPAA, GDPR).
- Report export to Excel or PDF.
- Any backup interval number.
- Material cost counted in clinic profit.
- "Dentists see only their own patients". The accurate wording is "own appointments, treatments and invoices".
