# Laptop UI rehearsal drivers (Playwright, real screens)

Run with the system Python that has Playwright (C:\Users\MSI\AppData\Local\Python\pythoncore-3.14-64\python.exe)
against the rehearsal till on 127.0.0.1:5010, signed in as desk-owner. Each script
prints what the screen showed and saves screenshots; none of them fakes a route.

- employees_invite.py look|modal|invite <email>  -- the Employees screen and its Create Invite dialog
- setup_link.py <setup-url> <password>          -- the invite link in a fresh browser (the Set your password page)
- products_add.py look|add <name> <sku> <price>  -- the Add Product dialog
- products_import.py look|run <csv>              -- the three-step Import wizard (sample-products.csv is Ahmed's sample)
- scanner_and_reports.py <barcode>               -- scanner test page, a wedge burst into the POS, the Reports screen
- scanner_characterize.py                        -- capture vs inter-key delay on the scanner test page
- owner_dashboard_admin_device.py               -- recent transactions and the admin-device banner (claims it)
- dashboard_as_cashier.py                        -- what a cashier sees on the dashboard
