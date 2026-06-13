package com.clipvault.app

import android.app.Application
import android.content.Context
import androidx.room.Room
import com.clipvault.app.data.AppDatabase

/** App singletons. Deliberately tiny — no DI framework for a self-use app. */
class ClipVaultApp : Application() {
    lateinit var db: AppDatabase
        private set

    override fun onCreate() {
        super.onCreate()
        instance = this
        db = Room.databaseBuilder(this, AppDatabase::class.java, "clipvault.db").build()
    }

    companion object {
        lateinit var instance: ClipVaultApp
            private set
        fun db(context: Context): AppDatabase =
            (context.applicationContext as ClipVaultApp).db
    }
}
