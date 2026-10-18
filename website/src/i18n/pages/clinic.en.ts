export const clinicEn = {
  hero: {
    status: 'Available now', // claims: clinic-claims.md (page chrome, no claim)
    title: 'Your clinic day, in one place.', // claims: clinic-claims.md Patient file
    accent: 'one place', // claims: clinic-claims.md Patient file
    lead: "Patient files, appointments, tooth charts and accounts, on the clinic's own computer. Works offline.", // claims: clinic-claims.md Works offline
  },
  rail: {
    title: 'A day at the clinic', // claims: clinic-claims.md (page chrome, no claim)
    moments: [
      {
        title: 'Book the appointment', // claims: clinic-claims.md Appointment calendar
        line: 'Appointment calendar with statuses and holidays, and scheduling for each dentist.', // claims: clinic-claims.md Appointment calendar, Per-dentist scheduling
      },
      {
        title: 'Open the patient file', // claims: clinic-claims.md Patient file
        line: 'Personal details, medical history, and X-ray and clinical photo storage.', // claims: clinic-claims.md Patient file
      },
      {
        title: 'Chart and plan the treatment', // claims: clinic-claims.md Tooth chart, Treatment plans
        line: 'Tooth chart with conditions and a customizable catalog. Treatment plans with goals, estimated cost and dates.', // claims: clinic-claims.md Tooth chart, Treatment plans
      },
      {
        title: 'The follow-up sheet and payment', // claims: clinic-claims.md Follow-up sheet
        line: 'Per visit: procedure, tooth number, price, discount, lab cost, payment and clinic profit.', // claims: clinic-claims.md Follow-up sheet
      },
      {
        title: 'End of day reports', // claims: clinic-claims.md Reports
        line: 'Weekly, monthly and custom-range reports, a dashboard, and reporting for each dentist.', // claims: clinic-claims.md Reports
      },
    ],
  },
  features: {
    title: 'What is inside', // claims: clinic-claims.md (page chrome, no claim)
    items: [
      {
        title: 'Patient records', // claims: clinic-claims.md Duplicate-patient warning
        line: 'Duplicate-patient warning, merge, and CSV patient import.', // claims: clinic-claims.md Duplicate-patient warning
      },
      {
        title: 'Scheduling for each dentist', // claims: clinic-claims.md Per-dentist scheduling
        line: 'A dentist sees only their own appointments, treatments and invoices, when enabled.', // claims: clinic-claims.md Per-dentist scheduling
      },
      {
        title: 'One account per patient', // claims: clinic-claims.md Account ledger
        line: 'One account ledger per patient with payment history, and a receivables report.', // claims: clinic-claims.md Account ledger, Receivables report
      },
      {
        title: 'Invoices and statements', // claims: clinic-claims.md Printed invoices
        line: 'Printed invoices and patient statements in Arabic and English.', // claims: clinic-claims.md Printed invoices
      },
      {
        title: 'Materials stock', // claims: clinic-claims.md Materials stock
        line: 'Restock, adjust, write-off, low stock, expiry tracking, and materials per procedure. Desktop only.', // claims: clinic-claims.md Materials stock
      },
      {
        title: 'Android phone app', // claims: clinic-claims.md Android phone app
        line: 'Patients, appointments, visits, billing, reports, tooth chart and plans, on your phone.', // claims: clinic-claims.md Android phone app
      },
    ],
  },
  works: {
    title: 'Phone and desktop, in sync', // claims: clinic-claims.md Sync
    line: 'The phone and the clinic desktop sync over the local network, the cloud or Bluetooth. The cloud option needs internet.', // claims: clinic-claims.md Sync
  },
  privacy: {
    title: 'Private and dependable', // claims: clinic-claims.md Encrypted database
    items: [
      "Patient database encrypted on the clinic's Windows PC.", // claims: clinic-claims.md Encrypted database
      'Roles (admin, dentist, staff) with granular per-user permissions.', // claims: clinic-claims.md Roles
      'Works offline. One-click backup and automatic backups.', // claims: clinic-claims.md Works offline, Backup
    ],
  },
  run: {
    title: 'What you need to run it', // claims: clinic-claims.md (page chrome, no claim)
    line: "Aura Clinic runs on the clinic's Windows PC. Add the Android phone app to sync with it.", // claims: clinic-claims.md Encrypted database, Android phone app
  },
  cta: {
    title: 'Book a free demo', // claims: clinic-claims.md (page chrome, no claim)
    lead: 'Tell us about your clinic and we will show you Aura Clinic.', // claims: clinic-claims.md (page chrome, no claim)
  },
} as const;
