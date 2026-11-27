plugins { id("com.android.application") }
android {
    namespace = "org.crysirror.player"
    compileSdk = 36
    defaultConfig {
        applicationId = "__APPLICATION_ID__"
        minSdk = 28
        targetSdk = 36
        versionCode = 1
        versionName = "1.0"
        ndk { abiFilters += listOf("__ABI__") }
    }
    buildTypes { debug { isMinifyEnabled = false } }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
}
