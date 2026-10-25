"""Passwords refused outright (customer policy). Only entries of 10 or more
characters matter: anything shorter already fails the length rule. Compared
lowercase, after stripping spaces. This is a starter list of the most-used long
passwords, not the ~10k list the plan names; swapping in a bigger one means
editing this one frozenset, nothing else."""
COMMON_PASSWORDS = frozenset("""
password12 password123 password1234 password12345 password123456 passw0rd123 p@ssword123 p@ssw0rd123
1234567890 12345678910 123456789012 1234567890123 0123456789 0987654321 9876543210 1122334455 1111111111
2222222222 0000000000 1q2w3e4r5t 1qaz2wsx3edc 1q2w3e4r5t6y qwertyuiop qwertyuiop123 qwerty12345 qwerty123456
qwertyuiopasdfghjkl asdfghjkl asdfghjkl123 zxcvbnm123 zxcvbnm1234 abcdefghij abcdefghijk abcd123456 abc1234567
abcdefg123 iloveyou123 iloveyou12 iloveyou1234 welcome123 welcome1234 welcome12345 letmein123 letmein1234
administrator admin12345 admin123456 admin1234567 administrator1 changeme123 changeme1234 changemenow
trustno1234 football123 baseball123 basketball1 superman123 batman12345 princess123 sunshine123 monkey12345
dragon12345 master12345 shadow12345 michael1234 jennifer123 charlie1234 jordan12345 jordan123456 amman123456
aura123456 aura1234567 auraretail1 auraretail123 retail12345 retail123456 actionaura1 actionaura123
company1234 company12345 business123 business1234 shopowner123 mypassword1 mypassword12 mypassword123
mypassword1234 newpassword1 newpassword12 newpassword123 testtest123 test1234567 test12345678 testing12345
password00 password01 password11 password99 password2024 password2025 password2026 passw0rd1234
iloveyou1 football1234 internet123 computer123 computer1234 whatever123 freedom123 starwars123 pokemon123
google12345 facebook123 samsung1234 iphone12345 android1234 windows1234 microsoft1 hello12345 hello123456
helloworld1 helloworld123 qazwsxedc qazwsxedc123 qazwsx12345 zaq12wsxcde3 !qaz2wsx3edc q1w2e3r4t5 q1w2e3r4t5y6
1234qwer1234 123456abcd abcd1234567 a123456789 a1234567890 aa123456789 1234abcd1234 123123123123 123123123
112233445566 121212121212 654321654321 987654321 9876543210 147258369 147258369 159753159753 741852963
""".split())
