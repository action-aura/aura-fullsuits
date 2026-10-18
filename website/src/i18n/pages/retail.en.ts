export const retailEn = {
  hero: {
    status: 'Available now',
    title: 'The till that keeps selling offline.',
    accent: 'offline',
    lead: 'Aura Retail is an Arabic-first till and shop system from Action Aura in Amman. It runs on your shop computer and your phone, keeps them in sync over the shop\'s own network, and keeps selling when the connection drops.',
  },
  rail: {
    title: 'A shopkeeper\'s day',
    moments: [
      {
        title: 'Open the till',
        line: 'Start the day with an opening cash balance. Every sale after it is counted against that balance.',
        shot: 'pos',
        alt: 'Aura Retail till screen ready for the first sale of the day',
      },
      {
        title: 'Scan and sell',
        line: 'Sell from the keyboard with a barcode scanner. Hold and resume bills, check a price, take cash, card, CliQ, transfer or on-account.',
        shot: 'products',
        alt: 'Aura Retail items list with barcodes and prices',
      },
      {
        title: 'The internet drops',
        line: 'The till keeps selling and printing on the shop computer. Sync finishes by itself when the connection returns.',
        shot: 'posNight',
        alt: 'Aura Retail till in the dark theme, mid-sale',
      },
      {
        title: 'Your phone mirrors the counter',
        line: 'Your computer and phone find each other on the shop network and stay in sync automatically.',
        shot: 'dashboard',
        alt: 'Aura Retail dashboard showing the day\'s sales',
      },
      {
        title: 'End of day',
        line: 'Run X and Z reports and reconcile the cash. Any difference needs your approval.',
        shot: 'reports',
        alt: 'Aura Retail reports screen with end of day figures',
      },
      {
        title: 'The books',
        line: 'Every sale is posted automatically to the trial balance, income, balance sheet and cash flow.',
        shot: 'trialBalance',
        alt: 'Aura Retail trial balance report',
      },
    ],
  },
  features: {
    title: 'Everything the shop needs',
    items: [
      {
        title: 'While you sell',
        line: 'Whole sale from the keyboard, barcode scanning, hold and resume, price check, and every way of paying.',
      },
      {
        title: 'Your stock',
        line: 'Items and barcodes, suppliers and purchase orders, and returns.',
      },
      {
        title: 'Money owed to you and by you',
        line: 'Credit accounts for customers and suppliers, statements, ageing, credit limits, cheques, loyalty points and promotions.',
      },
      {
        title: 'End of day',
        line: 'Opening balance, X and Z reports and cash reconciliation. Every sale is posted to your books.',
      },
      {
        title: 'Branches and devices',
        line: 'Devices find each other on the shop network, and stock moves between branches.',
      },
      {
        title: 'Staff roles',
        line: 'Owner, manager, cashier and accountant roles, with an audit log of who did what.',
      },
    ],
  },
  works: {
    title: 'Works with the hardware you have',
    line: 'Barcode scanners and receipt printers connect to the shop computer.',
  },
  jordan: {
    title: 'Ready for Jordan',
    items: [
      'Amounts to the fils, three decimal places',
      '16% sales tax by default',
      'CliQ payments',
      'Full right-to-left Arabic interface',
    ],
  },
  run: {
    title: 'What you need to run it',
    line: 'A Windows computer for the counter, an Android phone if you want one, and your shop network. We install, move your data and train your staff.',
  },
  cta: {
    title: 'Tell us about your shop.',
    lead: 'Our team in Amman installs, trains and supports. Pricing depends on your shop. Ask us.',
  },
} as const;
