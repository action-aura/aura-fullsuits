# Chaquopy + WebView. Minification is off for release by default (see build.gradle),
# but keep these rules so enabling R8 later doesn't strip the bridge classes.
-keep class com.chaquo.python.** { *; }
-keep class com.actionaura.clinic.** { *; }
-keepclassmembers class * {
    @android.webkit.JavascriptInterface <methods>;
}
