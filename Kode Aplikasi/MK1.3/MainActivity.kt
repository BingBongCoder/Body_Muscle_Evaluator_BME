package com.example.bme // Sesuaikan dengan package-mu

import android.graphics.Color
import android.os.Bundle
import android.view.View
import android.widget.*
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import com.github.mikephil.charting.charts.LineChart
import com.github.mikephil.charting.components.AxisBase
import com.github.mikephil.charting.data.Entry
import com.github.mikephil.charting.data.LineData
import com.github.mikephil.charting.data.LineDataSet
import com.github.mikephil.charting.formatter.ValueFormatter
import com.google.android.material.bottomnavigation.BottomNavigationView
import kotlinx.coroutines.*
import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.ServerSocket
import java.net.Socket
import java.util.concurrent.CopyOnWriteArrayList

class MainActivity : AppCompatActivity() {

    private val connectedClients = CopyOnWriteArrayList<Socket>()
    private var serverJob: Job? = null
    private val PORT = 8080

    // Data Grafik
    private val entries1 = ArrayList<Entry>()
    private val entries2 = ArrayList<Entry>()
    private lateinit var lineDataSet1: LineDataSet
    private lateinit var lineDataSet2: LineDataSet
    private var timeIndex1 = 0f
    private var timeIndex2 = 0f

    // Optimasi Sinyal EMG (2500 Hz)
    private var updateCounter = 0
    private var cameraLength = 25000f
    private var maxBuffer = 30000
    private var isFullScreen = false

    // HIDE SPIKE LOGIC
    private var mosfetOnTime: Long = 0
    private val SPIKE_IGNORE_DURATION = 500L

    // Memori Sinkronisasi Warna LED
    private var esp1Colors = intArrayOf(0, 0, 0, 0, 0, 0)
    private var esp2Colors = intArrayOf(0, 0, 0, 0, 0, 0)

    private lateinit var layoutHome: View
    private lateinit var layoutPlot: View
    private lateinit var layoutSettings: View

    private lateinit var tvBattery1: TextView
    private lateinit var tvBattery2: TextView
    private lateinit var btnStartServer: Button
    private lateinit var tvConsole: TextView
    private lateinit var lineChart: LineChart
    private lateinit var swViewMode: Switch
    private lateinit var rgPlotSelect: RadioGroup
    private lateinit var rlChartContainer: RelativeLayout
    private lateinit var svConsole: ScrollView
    private lateinit var btnFullScreen: Button
    private lateinit var spnEspSelect: Spinner

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        supportActionBar?.hide()
        window.statusBarColor = Color.parseColor("#121212")
        WindowCompat.getInsetsController(window, window.decorView).isAppearanceLightStatusBars = false
        setContentView(R.layout.activity_main)

        val container = findViewById<FrameLayout>(R.id.fragment_container)

        ViewCompat.setOnApplyWindowInsetsListener(container) { view, insets ->
            val statusBar = insets.getInsets(WindowInsetsCompat.Type.statusBars())
            view.setPadding(view.paddingLeft, statusBar.top, view.paddingRight, view.paddingBottom)
            insets
        }

        val bottomNav = findViewById<BottomNavigationView>(R.id.bottom_navigation)

        layoutHome = layoutInflater.inflate(R.layout.layout_home, null)
        layoutPlot = layoutInflater.inflate(R.layout.layout_plot, null)
        layoutSettings = layoutInflater.inflate(R.layout.layout_settings, null)

        container.addView(layoutHome)
        container.addView(layoutPlot)
        container.addView(layoutSettings)

        setupHomeUI()
        setupPlotUI()
        setupSettingsUI()
        showTab("home")

        bottomNav.setOnItemSelectedListener { item ->
            when (item.itemId) {
                R.id.nav_home -> {
                    showTab("home")
                    sendCommandToAll("MOS:0")
                    true
                }
                R.id.nav_plot -> {
                    showTab("plot")
                    sendCommandToAll("MOS:1")
                    mosfetOnTime = System.currentTimeMillis()
                    true
                }
                R.id.nav_settings -> {
                    showTab("settings")
                    sendCommandToAll("MOS:0")
                    true
                }
                else -> false
            }
        }

        // Memulai server secara otomatis di latar belakang untuk bersiaga
        if (serverJob == null || serverJob?.isActive == false) {
            startServer()
        }
    }

    private fun showTab(tab: String) {
        layoutHome.visibility = if (tab == "home") View.VISIBLE else View.GONE
        layoutPlot.visibility = if (tab == "plot") View.VISIBLE else View.GONE
        layoutSettings.visibility = if (tab == "settings") View.VISIBLE else View.GONE
    }

    private fun setupHomeUI() {
        btnStartServer = layoutHome.findViewById(R.id.btnStartServer)
        tvBattery1 = layoutHome.findViewById(R.id.tvBattery1)
        tvBattery2 = layoutHome.findViewById(R.id.tvBattery2) ?: tvBattery1

        val etCamera = layoutHome.findViewById<EditText>(R.id.etCameraLength)

        layoutHome.findViewById<Button>(R.id.btnSetRange)?.setOnClickListener {
            val minV = layoutHome.findViewById<EditText>(R.id.etMinPlot).text.toString().toIntOrNull() ?: 0
            val maxV = layoutHome.findViewById<EditText>(R.id.etMaxPlot).text.toString().toIntOrNull() ?: 3500

            if (maxV > minV) {
                lineChart.axisLeft.axisMinimum = minV.toFloat()
                lineChart.axisLeft.axisMaximum = maxV.toFloat()
                lineChart.invalidate()
                Toast.makeText(this, "Rentang magnitudo diubah: $minV - $maxV mV", Toast.LENGTH_SHORT).show()
            }
        }

        layoutHome.findViewById<Button>(R.id.btnSetCamera)?.setOnClickListener {
            val newCamSeconds = etCamera?.text.toString().toIntOrNull()

            if (newCamSeconds != null && newCamSeconds > 0) {
                cameraLength = (newCamSeconds * 2500).toFloat()
                maxBuffer = maxOf((cameraLength * 1.2f).toInt(), 5000)

                lineChart.setVisibleXRangeMaximum(cameraLength)
                lineChart.setVisibleXRangeMinimum(cameraLength)

                val maxTime = maxOf(timeIndex1, timeIndex2)
                val scrollTarget = if (maxTime > cameraLength) maxTime - cameraLength else 0f
                lineChart.moveViewToX(scrollTarget)
                lineChart.invalidate()

                Toast.makeText(this, "Lebar kamera diatur ke $newCamSeconds detik", Toast.LENGTH_SHORT).show()
            }
        }

        // =========================================================
        // PERUBAHAN LOGIKA TOMBOL CONNECT
        // =========================================================
        btnStartServer.setOnClickListener {
            if (connectedClients.isNotEmpty()) {
                btnStartServer.text = "BME Connected"
                btnStartServer.isEnabled = false
            } else {
                Toast.makeText(this, "Belum ada BME yang terhubung dengan Hotspot HP", Toast.LENGTH_SHORT).show()
                if (serverJob == null || serverJob?.isActive == false) {
                    startServer()
                }
            }
        }

        val sliders = arrayOf(R.id.sbBrightness1, R.id.sbBrightness2, R.id.sbBrightness3)
        val texts = arrayOf(R.id.tvBrightness1, R.id.tvBrightness2, R.id.tvBrightness3)
        val colorLabels = arrayOf("LED Merah (R)", "LED Hijau (G)", "LED Biru (B)")
        val pwmCommands = arrayOf("PWM1", "PWM2", "PWM3")

        for (i in 0..2) {
            val sb = layoutHome.findViewById<SeekBar>(sliders[i])
            val tv = layoutHome.findViewById<TextView>(texts[i])

            sb?.setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
                override fun onProgressChanged(s: SeekBar?, p: Int, f: Boolean) {
                    tv?.text = "${colorLabels[i]}: $p"
                }
                override fun onStartTrackingTouch(s: SeekBar?) {}
                override fun onStopTrackingTouch(s: SeekBar?) {
                    sendCommandToAll("${pwmCommands[i]}:${s?.progress ?: 0}")
                }
            })
        }
    }

    private fun setupSettingsUI() {
        spnEspSelect = layoutSettings.findViewById(R.id.spnEspSelect)
        val adapter = ArrayAdapter(this, android.R.layout.simple_spinner_item, arrayOf("Target: Konfigurasi ESP 1", "Target: Konfigurasi ESP 2"))
        adapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item)
        spnEspSelect.adapter = adapter

        spnEspSelect.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
            override fun onItemSelected(parent: AdapterView<*>?, view: View?, position: Int, id: Long) {
                updateSlidersFromMemory(position + 1)
            }
            override fun onNothingSelected(parent: AdapterView<*>?) {}
        }

        val sliderIds = arrayOf(R.id.sbSrchR, R.id.sbSrchG, R.id.sbSrchB, R.id.sbConnR, R.id.sbConnG, R.id.sbConnB)
        val textIds = arrayOf(R.id.tvSrchR, R.id.tvSrchG, R.id.tvSrchB, R.id.tvConnR, R.id.tvConnG, R.id.tvConnB)
        val colorNames = arrayOf("Red", "Green", "Blue", "Red", "Green", "Blue")
        val commands = arrayOf("SR_R", "SR_G", "SR_B", "CN_R", "CN_G", "CN_B")

        for (i in 0..5) {
            val sb = layoutSettings.findViewById<SeekBar>(sliderIds[i])
            val tv = layoutSettings.findViewById<TextView>(textIds[i])
            sb?.setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
                override fun onProgressChanged(s: SeekBar?, p: Int, fromUser: Boolean) {
                    tv?.text = "${colorNames[i]}: $p"
                    if (fromUser) {
                        val espId = spnEspSelect.selectedItemPosition + 1
                        if (espId == 1) esp1Colors[i] = p else esp2Colors[i] = p
                    }
                }
                override fun onStartTrackingTouch(s: SeekBar?) {}
                override fun onStopTrackingTouch(s: SeekBar?) {
                    val espId = spnEspSelect.selectedItemPosition + 1
                    sendCommandToAll("${espId}_${commands[i]}:${s?.progress ?: 0}")
                }
            })
        }

        val swFatigueMode = layoutSettings.findViewById<Switch>(R.id.swFatigueMode)
        swFatigueMode?.setOnCheckedChangeListener { _, isChecked ->
            if (isChecked) {
                Toast.makeText(this, "Fatigue Mode: ON", Toast.LENGTH_SHORT).show()
            } else {
                Toast.makeText(this, "Fatigue Mode: OFF", Toast.LENGTH_SHORT).show()
            }
        }
    }

    private fun updateSlidersFromMemory(espId: Int) {
        val colors = if (espId == 1) esp1Colors else esp2Colors
        val sliderIds = arrayOf(R.id.sbSrchR, R.id.sbSrchG, R.id.sbSrchB, R.id.sbConnR, R.id.sbConnG, R.id.sbConnB)
        for (i in 0..5) {
            val sb = layoutSettings.findViewById<SeekBar>(sliderIds[i])
            sb?.progress = colors[i]
        }
    }

    private fun syncColorsFromESP(espId: Int, data: String) {
        val parts = data.split(",")
        if (parts.size == 6) {
            val colors = if (espId == 1) esp1Colors else esp2Colors
            for (i in 0..5) colors[i] = parts[i].trim().toIntOrNull() ?: 0

            runOnUiThread {
                if (spnEspSelect.selectedItemPosition + 1 == espId) {
                    updateSlidersFromMemory(espId)
                }
            }
        }
    }

    private fun setupPlotUI() {
        tvConsole = layoutPlot.findViewById(R.id.tvConsole)
        lineChart = layoutPlot.findViewById(R.id.lineChart)
        swViewMode = layoutPlot.findViewById(R.id.swViewMode)
        rgPlotSelect = layoutPlot.findViewById(R.id.rgPlotSelect)
        rlChartContainer = layoutPlot.findViewById(R.id.rlChartContainer)
        svConsole = layoutPlot.findViewById(R.id.svConsole)
        btnFullScreen = layoutPlot.findViewById(R.id.btnFullScreen)

        swViewMode.setOnCheckedChangeListener { _, isChecked ->
            rlChartContainer.visibility = if (isChecked) View.VISIBLE else View.GONE
            svConsole.visibility = if (isChecked) View.GONE else View.VISIBLE
        }

        btnFullScreen.setOnClickListener {
            isFullScreen = !isFullScreen
            val bottomNav = findViewById<BottomNavigationView>(R.id.bottom_navigation)
            if (isFullScreen) {
                supportActionBar?.hide(); bottomNav.visibility = View.GONE; swViewMode.visibility = View.GONE; rgPlotSelect.visibility = View.GONE
                btnFullScreen.text = "EXIT FULL SCREEN"
                window.decorView.systemUiVisibility = (View.SYSTEM_UI_FLAG_FULLSCREEN or View.SYSTEM_UI_FLAG_HIDE_NAVIGATION or View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY)
            } else {
                supportActionBar?.show(); bottomNav.visibility = View.VISIBLE; swViewMode.visibility = View.VISIBLE; rgPlotSelect.visibility = View.VISIBLE
                btnFullScreen.text = "[ ] FULL SCREEN"
                window.decorView.systemUiVisibility = View.SYSTEM_UI_FLAG_VISIBLE
            }
        }

        if (entries1.isEmpty()) entries1.add(Entry(timeIndex1++, 0f))
        if (entries2.isEmpty()) entries2.add(Entry(timeIndex2++, 0f))

        lineDataSet1 = LineDataSet(entries1, "ESP 1").apply { color = Color.RED; setDrawCircles(false); setDrawValues(false); lineWidth = 1.5f }
        lineDataSet2 = LineDataSet(entries2, "ESP 2").apply { color = Color.BLUE; setDrawCircles(false); setDrawValues(false); lineWidth = 1.5f }

        lineChart.data = LineData(lineDataSet1, lineDataSet2)
        lineChart.description.isEnabled = false
        lineChart.axisRight.isEnabled = false
        lineChart.axisLeft.axisMinimum = 0f
        lineChart.axisLeft.axisMaximum = 3500f

        lineChart.xAxis.textColor = Color.WHITE
        lineChart.axisLeft.textColor = Color.WHITE
        lineChart.legend.textColor = Color.WHITE

        lineChart.xAxis.valueFormatter = object : ValueFormatter() {
            override fun getAxisLabel(value: Float, axis: AxisBase?): String {
                val timeInSeconds = value / 2500f
                return String.format("%.1f s", timeInSeconds)
            }
        }

        rgPlotSelect.setOnCheckedChangeListener { _, checkedId ->
            when (checkedId) {
                R.id.rbEsp1 -> { lineDataSet1.isVisible = true; lineDataSet2.isVisible = false }
                R.id.rbEsp2 -> { lineDataSet1.isVisible = false; lineDataSet2.isVisible = true }
                R.id.rbBoth -> { lineDataSet1.isVisible = true; lineDataSet2.isVisible = true }
            }
            lineChart.invalidate()
        }
    }

    private fun sendCommandToAll(cmd: String) {
        CoroutineScope(Dispatchers.IO).launch {
            for (socket in connectedClients) {
                try { if (socket.isConnected && !socket.isClosed) { socket.getOutputStream().write(("$cmd\n").toByteArray()); socket.getOutputStream().flush() } } catch (e: Exception) { }
            }
        }
    }

    private fun startServer() {
        serverJob = CoroutineScope(Dispatchers.IO).launch {
            try {
                val ss = ServerSocket(PORT)
                withContext(Dispatchers.Main) { tvConsole.append("Server started...\n") }
                while (isActive) {
                    val client = ss.accept() // Ini akan menunggu sampai ESP terhubung
                    connectedClients.add(client)

                    // =========================================================
                    // OTOMATIS MENGUBAH UI TOMBOL SAAT ESP BERHASIL MASUK
                    // =========================================================
                    withContext(Dispatchers.Main) {
                        btnStartServer.text = "BME Connected"
                        btnStartServer.isEnabled = false
                    }

                    launch {
                        delay(500)
                        val mosfetCmd = if (layoutPlot.visibility == View.VISIBLE) {
                            mosfetOnTime = System.currentTimeMillis()
                            "MOS:1\n"
                        } else {
                            "MOS:0\n"
                        }
                        try { client.getOutputStream().write(mosfetCmd.toByteArray()); client.getOutputStream().flush() } catch (e: Exception) {}
                        readClientData(client)
                    }
                }
            } catch (e: Exception) { }
        }
    }

    private suspend fun CoroutineScope.readClientData(client: Socket) {
        try {
            val reader = BufferedReader(InputStreamReader(client.inputStream))
            while (isActive && client.isConnected) {
                val line = reader.readLine() ?: break
                withContext(Dispatchers.Main) {
                    when {
                        line.startsWith("B1:") -> tvBattery1.text = "Bat 1: ${line.substring(3).trim()}%"
                        line.startsWith("B2:") -> tvBattery2.text = "Bat 2: ${line.substring(3).trim()}%"

                        line.startsWith("E1:") -> updateChart(1, line.substring(3), line)
                        line.startsWith("E2:") -> updateChart(2, line.substring(3), line)
                        line.startsWith("SYNC1:") -> syncColorsFromESP(1, line.substring(6))
                        line.startsWith("SYNC2:") -> syncColorsFromESP(2, line.substring(6))
                        else -> { if (layoutPlot.visibility == View.VISIBLE && !swViewMode.isChecked) tvConsole.append("Unknown: $line\n") }
                    }
                }
            }
        } catch (e: Exception) {
        } finally {
            connectedClients.remove(client)
            client.close()

            // =========================================================
            // KEMBALIKAN TOMBOL JIKA KONEKSI TERPUTUS
            // =========================================================
            withContext(Dispatchers.Main) {
                if (connectedClients.isEmpty()) {
                    btnStartServer.text = "Connect to BME"
                    btnStartServer.isEnabled = true
                }
            }
        }
    }

    private fun updateChart(espId: Int, valueStr: String, rawLine: String) {
        if (layoutPlot.visibility == View.VISIBLE && !swViewMode.isChecked) {
            tvConsole.append("$rawLine\n")
            if (tvConsole.text.length > 3000) tvConsole.text = tvConsole.text.substring(1500)
        }

        if (System.currentTimeMillis() - mosfetOnTime < SPIKE_IGNORE_DURATION) {
            return
        }

        val valuesArray = valueStr.split(",")
        var hasNewData = false

        for (vStr in valuesArray) {
            val value = vStr.trim().replace(",", ".").toFloatOrNull() ?: continue
            if (espId == 1) {
                entries1.add(Entry(timeIndex1++, value))
            } else {
                entries2.add(Entry(timeIndex2++, value))
            }
            hasNewData = true
        }

        if (espId == 1 && entries1.size > maxBuffer) {
            entries1.subList(0, entries1.size - maxBuffer).clear()
        } else if (espId == 2 && entries2.size > maxBuffer) {
            entries2.subList(0, entries2.size - maxBuffer).clear()
        }

        if (hasNewData) {
            if (espId == 1) lineDataSet1.notifyDataSetChanged()
            else lineDataSet2.notifyDataSetChanged()

            updateCounter++

            if (updateCounter % 5 == 0) {
                lineChart.data.notifyDataChanged()
                lineChart.notifyDataSetChanged()

                lineChart.setVisibleXRangeMaximum(cameraLength)
                lineChart.setVisibleXRangeMinimum(cameraLength)

                val maxTime = maxOf(timeIndex1, timeIndex2)
                val scrollTarget = if (maxTime > cameraLength) maxTime - cameraLength else 0f
                lineChart.moveViewToX(scrollTarget)

                if (layoutPlot.visibility == View.VISIBLE && swViewMode.isChecked) {
                    lineChart.invalidate()
                }
            }
        }
    }

    override fun onDestroy() { super.onDestroy(); serverJob?.cancel() }
}
