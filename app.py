import json
import os
from flask import Flask, render_template, request, redirect, url_for, session
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
import pymysql  # pastikan terimport

app = Flask(__name__)
app.secret_key = "awikwok"

app.config["SQLALCHEMY_DATABASE_URI"] = "mysql+pymysql://root:@localhost/bengkel_db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

class SparepartDB(db.Model):
    __tablename__ = "spareparts"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    price = db.Column(db.Float, nullable=False)
    stock = db.Column(db.Integer, default=0)

class ServiceDB(db.Model):
    __tablename__ = "services"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    price = db.Column(db.Float, nullable=False)
    description = db.Column(db.Text)

class TransactionDB(db.Model):
    __tablename__ = "transactions"

    id = db.Column(db.Integer, primary_key=True)

    # Waktu & customer
    date = db.Column(db.Date, nullable=False)
    customer_username = db.Column(db.String(50), nullable=False)
    customer = db.Column(db.String(100), nullable=False)

    # Relasi layanan
    service_id = db.Column(db.Integer, db.ForeignKey("services.id"), nullable=False)
    service_name = db.Column(db.String(100), nullable=False)
    price_service = db.Column(db.Float, default=0)

    # Relasi sparepart (1 transaksi maksimal 1 jenis sparepart)
    sparepart_id = db.Column(db.Integer, db.ForeignKey("spareparts.id"))
    sparepart_name = db.Column(db.String(255))      # disimpan untuk tampilan
    price_spare = db.Column(db.Float, default=0)

    # Total & status
    total = db.Column(db.Float, default=0)
    status = db.Column(db.String(50), default="Proses")

    # Penugasan karyawan (opsional)
    employee_id = db.Column(db.Integer)
    employee_name = db.Column(db.String(100))

    # Relasi objek
    service = db.relationship("ServiceDB")
    sparepart = db.relationship("SparepartDB")



class UserDB(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)  # sekarang masih plain
    full_name = db.Column(db.String(100))
    email = db.Column(db.String(100))
    role = db.Column(db.String(20), nullable=False)  # owner/admin/employee/customer

class EmployeeDB(db.Model):
    __tablename__ = "employees"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    name = db.Column(db.String(100), nullable=False)
    position = db.Column(db.String(50), nullable=False)   # Mekanik / Admin dll
    status = db.Column(db.String(20), default="Aktif")    # Aktif / Cuti / Nonaktif

    user = db.relationship("UserDB")

class BookingDB(db.Model):
    __tablename__ = "bookings"
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    date = db.Column(db.Date, nullable=False)
    time = db.Column(db.Time, nullable=False)
    service_id = db.Column(db.Integer, db.ForeignKey("services.id"), nullable=False)
    note = db.Column(db.Text)
    status = db.Column(db.String(50), default="Menunggu Konfirmasi")

    customer = db.relationship("UserDB")
    service = db.relationship("ServiceDB")

class BookingItemDB(db.Model):
    __tablename__ = "booking_items"
    id = db.Column(db.Integer, primary_key=True)
    booking_id = db.Column(db.Integer, db.ForeignKey("bookings.id"), nullable=False)
    sparepart_id = db.Column(db.Integer, db.ForeignKey("spareparts.id"), nullable=False)
    qty = db.Column(db.Integer, nullable=False, default=1)

    booking = db.relationship("BookingDB", backref="items")
    sparepart = db.relationship("SparepartDB")

class AttendanceDB(db.Model):
    __tablename__ = "attendance"
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey("employees.id"), nullable=False)
    date = db.Column(db.Date, nullable=False)
    check_in = db.Column(db.Time)
    check_out = db.Column(db.Time)

    employee = db.relationship("EmployeeDB")

from datetime import date, timedelta
from sqlalchemy import func

LEAD_TIME_DAYS = 4  # asumsi lead time sama untuk semua sparepart

def get_daily_usage(sparepart_id, days_back=30):
    today = date.today()
    start_date = today - timedelta(days=days_back)

    rows = (
        db.session.query(
            TransactionDB.date.label("d"),
            func.count(TransactionDB.id).label("qty")  # 1 transaksi = 1 pemakaian sparepart
        )
        .filter(
            TransactionDB.sparepart_id == sparepart_id,
            TransactionDB.date >= start_date
        )
        .group_by(TransactionDB.date)
        .all()
    )

    usage_by_day = {r.d: int(r.qty) for r in rows}
    series = []
    for i in range(days_back):
        d = start_date + timedelta(days=i)
        series.append(usage_by_day.get(d, 0))
    return series


def hitung_rop(sparepart_id, days_back=30, lead_time=LEAD_TIME_DAYS):
    """
    Menghitung AU, pemakaian maks, safety stock, dan ROP untuk 1 sparepart.
    Menggunakan data pemakaian N hari terakhir.
    """
    series = get_daily_usage(sparepart_id, days_back)
    if not series:
        return 0, 0, 0, 0  # belum ada data

    total = sum(series)
    n = len(series)

    avg_usage = total / n                # AU = pemakaian rata-rata
    max_usage = max(series)             # pemakaian maksimum
    safety_stock = (max_usage - avg_usage) * lead_time
    rop = (lead_time * avg_usage) + safety_stock

    # dibulatkan ke atas supaya aman
    return (
        round(avg_usage, 2),
        int(max_usage),
        int(round(safety_stock)),
        int(round(rop)),
    )



@app.route("/")
def index():
    return render_template("index.html")
@app.route("/about")
def about():
    return render_template("about.html")
@app.route("/services")
def services():
    return render_template("services.html")
@app.route("/contact")
def contact():
    return render_template("contact.html")
@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        user = UserDB.query.filter_by(username=username, password=password).first()
        if user is None:
            error = "Username atau password salah."
        else:
            session["user_id"] = user.id
            session["username"] = user.username
            session["role"] = user.role
            if user.role == "owner":
                return redirect(url_for("owner_dashboard"))
            elif user.role == "admin":
                return redirect(url_for("admin_dashboard"))
            elif user.role == "employee":
                return redirect(url_for("employee_dashboard"))
            elif user.role == "customer":
                return redirect(url_for("customer_dashboard"))
            else:
                return redirect(url_for("index"))
    return render_template("login.html", error=error)


@app.route("/register", methods=["GET", "POST"])
def register():
    message = None
    if request.method == "POST":
        fullname = request.form.get("fullname")
        email = request.form.get("email")
        username = request.form.get("username")
        password = request.form.get("password")
        confirm = request.form.get("confirm_password")
        if password != confirm:
            message = "Password dan konfirmasi tidak sama."
        else:
            existing = UserDB.query.filter_by(username=username).first()
            if existing:
                message = "Username sudah digunakan."
            else:
                user = UserDB(
                    username=username,
                    password=password,
                    full_name=fullname,
                    email=email,
                    role="customer" 
                )
                db.session.add(user)
                db.session.commit()
                return redirect(url_for("login"))
    return render_template("register.html", message=message)


@app.route("/owner-dashboard")
def owner_dashboard():
    if session.get("role") != "owner":
        return redirect(url_for("login"))

    employees = EmployeeDB.query.all()
    spareparts = SparepartDB.query.all()
    transactions = TransactionDB.query.all()

    # Hitung ROP per sparepart
    rop_map = {}
    for sp in spareparts:
        avg_use, max_use, ss, rop = hitung_rop(sp.id)  # pakai fungsi di atas
        rop_map[sp.id] = {
            "avg": avg_use,
            "max": max_use,
            "ss": ss,
            "rop": rop,
        }

    # Stok rendah berdasarkan ROP: stok <= ROP dan ROP > 0
    low_stock_list = [
        sp for sp in spareparts
        if rop_map.get(sp.id, {}).get("rop", 0) > 0
        and (sp.stock or 0) <= rop_map[sp.id]["rop"]
    ]

    today = datetime.today()
    this_month = (today.year, today.month)
    last_month = (
        today.year if today.month > 1 else today.year - 1,
        today.month - 1 if today.month > 1 else 12,
    )

    total_this_month = 0
    total_last_month = 0
    count_this_month = 0
    count_last_month = 0

    for t in transactions:
        d = t.date
        if not d:
            continue
        key = (d.year, d.month)
        if key == this_month:
            total_this_month += (t.total or 0)
            count_this_month += 1
        elif key == last_month:
            total_last_month += (t.total or 0)
            count_last_month += 1

    revenue_change_pct = (
        (total_this_month - total_last_month) / total_last_month * 100
        if total_last_month > 0 else 0
    )
    trx_change_pct = (
        (count_this_month - count_last_month) / count_last_month * 100
        if count_last_month > 0 else 0
    )

    active_employees = sum(1 for e in employees if (e.status or "") == "Aktif")
    low_stock_items = len(low_stock_list)
    pending_orders = sum(1 for t in transactions if (t.status or "") == "Proses")
    completed_orders = sum(1 for t in transactions if (t.status or "") == "Selesai")

    stats = {
        "total_transactions": count_this_month,
        "monthly_revenue": total_this_month,
        "active_employees": active_employees,
        "low_stock_items": low_stock_items,
        "pending_orders": pending_orders,
        "completed_orders": completed_orders,
        "trx_change_pct": revenue_change_pct and trx_change_pct,
        "revenue_change_pct": revenue_change_pct,
    }

    chart_labels = []
    chart_values = []
    daily = {}
    for t in transactions:
        d = t.date
        if not d:
            continue
        if d.year == this_month[0] and d.month == this_month[1]:
            key = d.day
            daily[key] = daily.get(key, 0) + (t.total or 0)

    for day in sorted(daily.keys()):
        chart_labels.append(str(day))
        chart_values.append(daily[day])

    return render_template(
        "owner/owner_dashboard.html",
        stats=stats,
        chart_labels=chart_labels,
        chart_values=chart_values,
        low_stock_list=low_stock_list,
        rop_map=rop_map,  # kalau mau ditampilkan di template
    )



@app.route("/owner/employees", methods=["GET", "POST"])
def manage_employees():
    if session.get("role") != "owner":
        return redirect(url_for("login"))
    message = None
    edit_employee_data = None
    employees = EmployeeDB.query.order_by(EmployeeDB.id.asc()).all()
    users = UserDB.query.order_by(UserDB.username.asc()).all()
    edit_id = request.args.get("edit_id", type=int)
    if edit_id:
        edit_employee_data = EmployeeDB.query.get(edit_id)
    if request.method == "POST":
        action = request.form.get("action")
        if action == "create":
            name = request.form.get("name")
            position = request.form.get("position")
            status = request.form.get("status")
            user_id = request.form.get("user_id", type=int)
            if not name or not position:
                message = "Nama dan posisi wajib diisi."
            else:
                emp = EmployeeDB(
                    name=name,
                    position=position,
                    status=status or "Aktif",
                    user_id=user_id if user_id else None
                )
                db.session.add(emp)
                db.session.commit()
                return redirect(url_for("manage_employees"))
        elif action == "update":
            emp_id = request.form.get("id", type=int)
            name = request.form.get("name")
            position = request.form.get("position")
            status = request.form.get("status")
            user_id = request.form.get("user_id", type=int)
            emp = EmployeeDB.query.get(emp_id)
            if emp is None:
                message = "Data karyawan tidak ditemukan."
            elif not name or not position:
                message = "Nama dan posisi wajib diisi."
            else:
                emp.name = name
                emp.position = position
                emp.status = status or "Aktif"
                emp.user_id = user_id if user_id else None
                db.session.commit()
                return redirect(url_for("manage_employees"))
        elif action == "delete":
            emp_id = request.form.get("id", type=int)
            emp = EmployeeDB.query.get(emp_id)
            if emp:
                db.session.delete(emp)
                db.session.commit()
            return redirect(url_for("manage_employees"))
    return render_template(
        "owner/employee_manage.html",
        employees=employees,
        users=users,
        message=message,
        edit_employee=edit_employee_data
    )


@app.route("/owner/services", methods=["GET", "POST"])
def manage_services():
    if session.get("role") != "owner":
        return redirect(url_for("login"))
    message = None
    edit_service = None
    services = ServiceDB.query.order_by(ServiceDB.id.asc()).all()
    edit_id = request.args.get("edit_id", type=int)
    if edit_id:
        edit_service = ServiceDB.query.get(edit_id)
    if request.method == "POST":
        action = request.form.get("action")
        if action == "create":
            name = request.form.get("name")
            price = request.form.get("price", type=float)
            description = request.form.get("description")
            if not name or price is None:
                message = "Nama layanan dan harga wajib diisi."
            else:
                existing = ServiceDB.query.filter(
                    db.func.lower(ServiceDB.name) == name.lower()
                ).first()
                if existing:
                    message = "Nama layanan sudah terdaftar."
                else:
                    srv = ServiceDB(
                        name=name,
                        price=price,
                        description=description or ""
                    )
                    db.session.add(srv)
                    db.session.commit()
                    return redirect(url_for("manage_services"))
        elif action == "update":
            srv_id = request.form.get("id", type=int)
            name = request.form.get("name")
            price = request.form.get("price", type=float)
            description = request.form.get("description")
            srv = ServiceDB.query.get(srv_id)
            if not srv:
                message = "Data layanan tidak ditemukan."
            elif not name or price is None:
                message = "Nama layanan dan harga wajib diisi."
            else:
                srv.name = name
                srv.price = price
                srv.description = description or ""
                db.session.commit()
                return redirect(url_for("manage_services"))
        elif action == "delete":
            srv_id = request.form.get("id", type=int)
            srv = ServiceDB.query.get(srv_id)
            if srv:
                db.session.delete(srv)
                db.session.commit()
            return redirect(url_for("manage_services"))
    return render_template(
        "owner/service_manage.html",
        services=services,
        message=message,
        edit_service=edit_service
    )


@app.route("/owner/spareparts", methods=["GET", "POST"])
def manage_spareparts():
    if session.get("role") != "owner":
        return redirect(url_for("login"))

    message = None
    edit_spare = None

    spareparts = SparepartDB.query.order_by(SparepartDB.id.asc()).all()
    edit_id = request.args.get("edit_id", type=int)
    if edit_id:
        edit_spare = SparepartDB.query.get(edit_id)

    if request.method == "POST":
        action = request.form.get("action")
        if action == "create":
            name = request.form.get("name")
            stock = request.form.get("stock", type=int)
            price = request.form.get("price", type=float)
            if not name or stock is None or price is None:
                message = "Nama, stok, dan harga wajib diisi."
            else:
                existing = SparepartDB.query.filter(
                    db.func.lower(SparepartDB.name) == name.lower()
                ).first()
                if existing:
                    existing.stock = (existing.stock or 0) + stock
                    existing.price = price
                    db.session.commit()
                else:
                    part = SparepartDB(name=name, stock=stock, price=price)
                    db.session.add(part)
                    db.session.commit()
                return redirect(url_for("manage_spareparts"))

        elif action == "update":
            sp_id = request.form.get("id", type=int)
            name = request.form.get("name")
            stock = request.form.get("stock", type=int)
            price = request.form.get("price", type=float)
            sp = SparepartDB.query.get(sp_id)
            if sp is None:
                message = "Data sparepart tidak ditemukan."
            elif not name or stock is None or price is None:
                message = "Nama, stok, dan harga wajib diisi."
            else:
                sp.name = name
                sp.stock = stock
                sp.price = price
                db.session.commit()
                return redirect(url_for("manage_spareparts"))

        elif action == "delete":
            sp_id = request.form.get("id", type=int)
            sp = SparepartDB.query.get(sp_id)
            if sp:
                # sebaiknya cegah hapus jika masih dipakai booking_items
                used = BookingItemDB.query.filter_by(sparepart_id=sp_id).first()
                if used:
                    message = "Sparepart masih dipakai di booking, tidak bisa dihapus."
                else:
                    db.session.delete(sp)
                    db.session.commit()
            return redirect(url_for("manage_spareparts"))

    # hitung ROP untuk setiap sparepart
    rop_map = {}
    for sp in spareparts:
        au, max_use, ss, rop = hitung_rop(sp.id)
        rop_map[sp.id] = {
            "avg": au,
            "max": max_use,
            "ss": ss,
            "rop": rop,
        }

    return render_template(
        "owner/sparepart_manage.html",
        spareparts=spareparts,
        message=message,
        edit_spare=edit_spare,
        rop_map=rop_map,
    )


@app.route("/owner/transactions", methods=["GET", "POST"])
def manage_transactions():
    if session.get("role") != "owner":
        return redirect(url_for("login"))

    services = ServiceDB.query.order_by(ServiceDB.name.asc()).all()
    spareparts = SparepartDB.query.order_by(SparepartDB.name.asc()).all()
    users = UserDB.query.filter_by(role="customer").order_by(UserDB.username.asc()).all()
    customers = users
    message = None
    edit_trx = None

    edit_id = request.args.get("edit_id", type=int)
    if edit_id:
        edit_trx = TransactionDB.query.get(edit_id)

    if request.method == "POST":
        action = request.form.get("action")

        if action == "create":
            date_str = request.form.get("date")
            customer_username = request.form.get("customer")
            service_id = request.form.get("service", type=int)
            spare_id = request.form.get("sparepart", type=int)
            status = request.form.get("status")

            customer_user = next((u for u in customers if u.username == customer_username), None)
            customer_name = customer_user.full_name if customer_user and customer_user.full_name else customer_username

            if not date_str or not customer_username or not service_id:
                message = "Tanggal, pelanggan, dan layanan wajib diisi."
            else:
                try:
                    date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
                except Exception:
                    message = "Format tanggal tidak valid (YYYY-MM-DD)."
                else:
                    service = ServiceDB.query.get(service_id)
                    service_price = service.price if service else 0

                    spare = SparepartDB.query.get(spare_id) if spare_id else None
                    spare_name = spare.name if spare else None
                    spare_price = spare.price if spare else 0
                    total = service_price + spare_price

                    if spare is not None:
                        if (spare.stock or 0) <= 0:
                            message = f"Stok {spare_name} sudah habis."
                            return render_template(
                                "owner/transaction_manage.html",
                                transactions=TransactionDB.query.order_by(TransactionDB.id.asc()).all(),
                                services=services,
                                spareparts=spareparts,
                                customers=customers,
                                message=message,
                                edit_trx=edit_trx,
                            )
                        spare.stock = (spare.stock or 0) - 1
                        db.session.commit()

                    trx = TransactionDB(
                        date=date_obj,
                        customer_username=customer_username,
                        customer=customer_name,
                        service_id=service.id if service else None,
                        service_name=service.name if service else "",
                        sparepart_id=spare.id if spare else None,
                        sparepart_name=spare_name,
                        price_service=service_price,
                        price_spare=spare_price,
                        total=total,
                        status=status or "Proses",
                    )
                    db.session.add(trx)
                    db.session.commit()
                    return redirect(url_for("manage_transactions"))

        elif action == "update":
            trx_id = request.form.get("id", type=int)
            date_str = request.form.get("date")
            customer_username = request.form.get("customer")
            service_id = request.form.get("service", type=int)
            spare_id = request.form.get("sparepart", type=int)
            status = request.form.get("status")

            # di sini customers adalah list objek UserDB, bukan dict
            customer_user = next((u for u in customers if u.username == customer_username), None)
            customer_name = customer_user.full_name if customer_user and customer_user.full_name else customer_username

            trx = TransactionDB.query.get(trx_id)

            if trx is None:
                message = "Data transaksi tidak ditemukan."
            elif not date_str or not customer_username or not service_id:
                message = "Tanggal, pelanggan, dan layanan wajib diisi."
            else:
                try:
                    date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
                except Exception:
                    message = "Format tanggal tidak valid (YYYY-MM-DD)."
                else:
                    service = ServiceDB.query.get(service_id)
                    service_price = service.price if service else 0

                    spare = SparepartDB.query.get(spare_id) if spare_id else None
                    spare_name = spare.name if spare else None
                    spare_price = spare.price if spare else 0
                    total = service_price + spare_price

                    trx.date = date_obj
                    trx.customer_username = customer_username
                    trx.customer = customer_name
                    trx.service_id = service.id if service else None
                    trx.service_name = service.name if service else ""
                    trx.sparepart_id = spare.id if spare else None
                    trx.sparepart_name = spare_name
                    trx.price_service = service_price
                    trx.price_spare = spare_price
                    trx.total = total
                    trx.status = status or "Proses"
                    db.session.commit()
                    return redirect(url_for("manage_transactions"))

        elif action == "delete":
            trx_id = request.form.get("id", type=int)
            trx = TransactionDB.query.get(trx_id)
            if trx:
                db.session.delete(trx)
                db.session.commit()
            return redirect(url_for("manage_transactions"))

    transactions = TransactionDB.query.order_by(TransactionDB.id.asc()).all()
    return render_template(
        "owner/transaction_manage.html",
        transactions=transactions,
        services=services,
        spareparts=spareparts,
        customers=customers,
        message=message,
        edit_trx=edit_trx,
    )



@app.route("/owner/reports")
def owner_reports():
    if session.get("role") != "owner":
        return redirect(url_for("login"))
    month = request.args.get("month", type=int)
    year = request.args.get("year", type=int)
    all_trx = TransactionDB.query.order_by(TransactionDB.date.desc()).all()
    if month and year:
        tx = []
        for t in all_trx:
            d = t.date
            if not d:
                continue
            if d.month == month and d.year == year:
                tx.append(t)
    else:
        tx = all_trx
    total_transaksi = len(tx)
    total_pendapatan = sum((t.total or 0) for t in tx)
    layanan_terlaris = {}
    for t in tx:
        nama = t.service_name or "-"
        layanan_terlaris[nama] = layanan_terlaris.get(nama, 0) + 1
    layanan_terlaris_list = sorted(
        [{"name": k, "count": v} for k, v in layanan_terlaris.items()],
        key=lambda x: x["count"],
        reverse=True
    )
    return render_template(
        "owner/report_manage.html",
        transactions=tx,
        total_transaksi=total_transaksi,
        total_pendapatan=total_pendapatan,
        layanan_terlaris=layanan_terlaris_list,
        selected_month=month,
        selected_year=year
    )


@app.route("/admin-dashboard")
def admin_dashboard():
    if session.get("role") != "admin":
        return redirect(url_for("login"))

    employees = EmployeeDB.query.all()
    spareparts = SparepartDB.query.all()
    transactions = TransactionDB.query.all()

    total_employees = len(employees)
    total_spareparts = len(spareparts)
    total_transactions = len(transactions)
    open_transactions = sum(1 for t in transactions if (t.status or "") == "Proses")

    # hitung ROP per sparepart
    rop_map = {}
    for sp in spareparts:
        avg_use, max_use, ss, rop = hitung_rop(sp.id)
        rop_map[sp.id] = {"avg": avg_use, "max": max_use, "ss": ss, "rop": rop}

    # stok menipis jika stok <= ROP dan ROP > 0
    low_stock_list = [
        sp for sp in spareparts
        if rop_map.get(sp.id, {}).get("rop", 0) > 0
        and (sp.stock or 0) <= rop_map[sp.id]["rop"]
    ]
    low_stock_items = len(low_stock_list)

    stats = {
        "total_employees": total_employees,
        "total_spareparts": total_spareparts,
        "total_transactions": total_transactions,
        "open_transactions": open_transactions,
        "low_stock_items": low_stock_items,
    }

    # buat data chart transaksi bulan ini (kode lamamu tetap)
    today = datetime.today()
    this_month = (today.year, today.month)
    daily = {}
    for t in transactions:
        d = t.date
        if not d:
            continue
        if d.year == this_month[0] and d.month == this_month[1]:
            key = d.day
            daily[key] = daily.get(key, 0) + (t.total or 0)

    chart_labels = [str(day) for day in sorted(daily.keys())]
    chart_values = [daily[day] for day in sorted(daily.keys())]

    return render_template(
        "admin/admin_dashboard.html",
        stats=stats,
        low_stock_list=low_stock_list,
        rop_map=rop_map,
        chart_labels=chart_labels,
        chart_values=chart_values,
    )



@app.route("/admin/jobs", methods=["GET", "POST"])
def admin_jobs():
    if session.get("role") != "admin":
        return redirect(url_for("login"))
    employees = EmployeeDB.query.filter_by(status="Aktif").all()
    transactions = TransactionDB.query.order_by(TransactionDB.id.asc()).all()
    bookings = BookingDB.query.order_by(BookingDB.date.desc(), BookingDB.time.desc()).all()
    services = ServiceDB.query.all()
    spareparts = SparepartDB.query.all()
    message = None
    active_emps = employees
    if request.method == "POST":
        action = request.form.get("action")
        if action == "create_from_booking":
            bid = request.form.get("booking_id", type=int)
            booking = BookingDB.query.get(bid)
            if booking is None:
                message = "Data booking tidak ditemukan."
            else:
                service = booking.service
                service_price = service.price if service else 0
                total_spare_price = 0
                spare_names = []
                for item in booking.items:
                    spare = item.sparepart
                    qty = item.qty or 0
                    if not spare or qty <= 0:
                        continue
                    if (spare.stock or 0) < qty:
                        message = f"Stok {spare.name} tidak cukup."
                        return render_template(
                            "admin/admin_jobs.html",
                            employees=active_emps,
                            transactions=transactions,
                            bookings=bookings,
                            message=message
                        )
                    spare.stock = (spare.stock or 0) - qty
                    total_spare_price += spare.price * qty
                    spare_names.append(f"{spare.name} x{qty}")
                db.session.commit()
                spare_text = ", ".join(spare_names) if spare_names else ""
                total = service_price + total_spare_price
                trx = TransactionDB(
                    date=booking.date,
                    customer_username=booking.customer.username,
                    customer=booking.customer.full_name or booking.customer.username,
                    service_id=service.id if service else None,
                    service_name=service.name if service else "",
                    sparepart_name=spare_text,
                    price_service=service_price,
                    price_spare=total_spare_price,
                    total=total,
                    status="Proses",
                )
                db.session.add(trx)
                booking.status = "Sudah dibuat transaksi"
                db.session.commit()
                return redirect(url_for("admin_jobs"))
        elif action == "assign_job":
            trx_id = request.form.get("trx_id", type=int)
            emp_id = request.form.get("emp_id", type=int)
            if not trx_id or not emp_id:
                message = "Transaksi dan karyawan wajib dipilih."
            else:
                trx = TransactionDB.query.get(trx_id)
                emp = EmployeeDB.query.get(emp_id)
                if trx is None or emp is None:
                    message = "Data transaksi atau karyawan tidak ditemukan."
                else:
                    trx.employee_id = emp.id
                    trx.employee_name = emp.name
                    if not trx.status:
                        trx.status = "Proses"
                    db.session.commit()
                    return redirect(url_for("admin_jobs"))
    transactions = TransactionDB.query.order_by(TransactionDB.id.asc()).all()
    bookings = BookingDB.query.order_by(BookingDB.date.desc(), BookingDB.time.desc()).all()
    return render_template(
        "admin/admin_jobs.html",
        employees=active_emps,
        transactions=transactions,
        bookings=bookings,
        message=message
    )


@app.route("/admin/stock", methods=["GET", "POST"])
def admin_stock():
    if session.get("role") != "admin":
        return redirect(url_for("login"))

    spareparts = SparepartDB.query.order_by(SparepartDB.name.asc()).all()
    message = None

    # Hitung ROP awal
    rop_map = {}
    for sp in spareparts:
        avg_use, max_use, ss, rop = hitung_rop(sp.id)
        rop_map[sp.id] = {"avg": avg_use, "max": max_use, "ss": ss, "rop": rop}

    # Stok menipis: stok <= ROP dan ROP > 0
    low_stock_list = [
        sp for sp in spareparts
        if rop_map.get(sp.id, {}).get("rop", 0) > 0
        and (sp.stock or 0) <= rop_map[sp.id]["rop"]
    ]

    if request.method == "POST":
        action = request.form.get("action")
        sp_id = request.form.get("id", type=int)

        if action == "restock" and sp_id is not None:
            qty = request.form.get("qty", type=int)
            sp = SparepartDB.query.get(sp_id)
            if sp is None:
                message = "Data sparepart tidak ditemukan."
            elif qty is None or qty <= 0:
                message = "Jumlah restock harus lebih dari 0."
            else:
                sp.stock = (sp.stock or 0) + qty
                db.session.commit()
                return redirect(url_for("admin_stock"))

    # Reload data setelah kemungkinan restock
    spareparts = SparepartDB.query.order_by(SparepartDB.name.asc()).all()
    rop_map = {}
    for sp in spareparts:
        avg_use, max_use, ss, rop = hitung_rop(sp.id)
        rop_map[sp.id] = {"avg": avg_use, "max": max_use, "ss": ss, "rop": rop}

    low_stock_list = [
        sp for sp in spareparts
        if rop_map.get(sp.id, {}).get("rop", 0) > 0
        and (sp.stock or 0) <= rop_map[sp.id]["rop"]
    ]

    return render_template(
        "admin/admin_stock.html",
        spareparts=spareparts,
        low_stock_list=low_stock_list,
        rop_map=rop_map,
        message=message,
    )



@app.route("/admin/report")
def admin_report():
    if session.get("role") != "admin":
        return redirect(url_for("login"))
    month = request.args.get("month", type=int)
    year = request.args.get("year", type=int)
    all_trx = TransactionDB.query.order_by(TransactionDB.date.desc()).all()
    if month and year:
        tx = []
        for t in all_trx:
            d = t.date
            if not d:
                continue
            if d.month == month and d.year == year:
                tx.append(t)
    else:
        tx = all_trx
    total_transaksi = len(tx)
    total_pendapatan = sum((t.total or 0) for t in tx)
    layanan_terlaris = {}
    for t in tx:
        nama = t.service_name or "-"
        layanan_terlaris[nama] = layanan_terlaris.get(nama, 0) + 1
    layanan_terlaris_list = sorted(
        [{"name": k, "count": v} for k, v in layanan_terlaris.items()],
        key=lambda x: x["count"],
        reverse=True
    )
    return render_template(
        "admin/admin_report.html",
        transactions=tx,
        total_transaksi=total_transaksi,
        total_pendapatan=total_pendapatan,
        layanan_terlaris=layanan_terlaris_list,
        selected_month=month,
        selected_year=year
    )


@app.route("/employee-dashboard")
def employee_dashboard():
    if session.get("role") != "employee":
        return redirect(url_for("login"))
    user_id = session.get("user_id")
    employee = EmployeeDB.query.filter_by(user_id=user_id).first()
    emp_id = employee.id if employee else None
    jobs = []
    if emp_id is not None:
        jobs = TransactionDB.query.filter_by(employee_id=emp_id).order_by(TransactionDB.date.desc()).all()
    total_jobs = len(jobs)
    jobs_in_progress = sum(1 for j in jobs if j.status == "Proses")
    jobs_done = sum(1 for j in jobs if j.status == "Selesai")
    stats = {
        "total_jobs": total_jobs,
        "jobs_in_progress": jobs_in_progress,
        "jobs_done": jobs_done,
    }
    return render_template(
        "employee/employee_dashboard.html",
        stats=stats,
        jobs=jobs,
        employee=employee
    )


@app.route("/employee/jobs/update", methods=["POST"])
def employee_update_job():
    if session.get("role") != "employee":
        return redirect(url_for("login"))
    trx_id = request.form.get("id", type=int)
    status = request.form.get("status")
    user_id = session.get("user_id")
    employee = EmployeeDB.query.filter_by(user_id=user_id).first()
    emp_id = employee.id if employee else None
    if emp_id is not None and trx_id:
        trx = TransactionDB.query.get(trx_id)
        if trx and trx.employee_id == emp_id and status in ["Proses", "Menunggu Sparepart", "Selesai"]:
            trx.status = status
            db.session.commit()
    return redirect(url_for("employee_dashboard"))


@app.route("/employee/attendance", methods=["GET", "POST"])
def employee_attendance():
    if session.get("role") != "employee":
        return redirect(url_for("login"))
    user_id = session.get("user_id")
    employee = EmployeeDB.query.filter_by(user_id=user_id).first()
    emp_id = employee.id if employee else None
    message = None
    today = datetime.today()
    today_str = today.strftime("%Y-%m-%d")
    today_date = today.date()
    if request.method == "POST" and emp_id is not None:
        status = request.form.get("status")
        now_time = datetime.now().time()
        if status not in ("Hadir", "Pulang"):
            message = "Silakan pilih aksi presensi yang benar."
        else:
            record = AttendanceDB.query.filter_by(
                employee_id=emp_id,
                date=today_date
            ).first()
            if record is None:
                record = AttendanceDB(
                    employee_id=emp_id,
                    date=today_date
                )
                db.session.add(record)
            if status == "Hadir" and record.check_in is None:
                record.check_in = now_time
            elif status == "Pulang":
                record.check_out = now_time
            db.session.commit()
            message = "Presensi berhasil disimpan."
    elif request.method == "POST" and emp_id is None:
        message = "Data karyawan tidak ditemukan. Cek kembali relasi user_id di tabel employees."
    records = AttendanceDB.query.filter_by(employee_id=emp_id) \
                                .order_by(AttendanceDB.date.desc()).all() if emp_id else []
    return render_template(
        "employee/employee_attendance.html",
        employee=employee,
        records=records,
        today=today_str,
        message=message
    )


@app.route("/customer-dashboard")
def customer_dashboard():
    if session.get("role") != "customer":
        return redirect(url_for("login"))
    user_id = session.get("user_id")
    user = UserDB.query.get(user_id)
    username = user.username if user else None
    full_name = user.full_name if user and user.full_name else (username or "Customer")
    if username:
        my_trx = TransactionDB.query.filter_by(customer_username=username) \
                                    .order_by(TransactionDB.date.desc()).all()
    else:
        my_trx = []
    total_transaksi = len(my_trx)
    total_biaya = sum((t.total or 0) for t in my_trx)
    selesai = sum(1 for t in my_trx if (t.status or "") == "Selesai")
    proses = sum(1 for t in my_trx if (t.status or "") == "Proses")
    stats = {
        "total_transaksi": total_transaksi,
        "total_biaya": total_biaya,
        "selesai": selesai,
        "proses": proses,
    }
    return render_template(
        "customer/customer_dashboard.html",
        stats=stats,
        transactions=my_trx,
        customer_name=full_name,
    )


@app.route("/customer/booking", methods=["GET", "POST"])
def customer_booking():
    if session.get("role") != "customer":
        return redirect(url_for("login"))
    services = ServiceDB.query.order_by(ServiceDB.name.asc()).all()
    spareparts = SparepartDB.query.order_by(SparepartDB.name.asc()).all()
    user_id = session.get("user_id")
    user = UserDB.query.get(user_id)
    full_name = user.full_name if user and user.full_name else (user.username if user else "Customer")
    message = None
    if request.method == "POST":
        date_str = request.form.get("date")
        time_str = request.form.get("time")
        service_name = request.form.get("service")  # kirim nama layanan
        note = request.form.get("note")
        cart_json = request.form.get("cart_json")
        try:
            cart = json.loads(cart_json) if cart_json else []
        except Exception:
            cart = []
        if not date_str or not time_str or not service_name:
            message = "Tanggal, waktu, dan jenis layanan wajib diisi."
        else:
            try:
                date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
                time_obj = datetime.strptime(time_str, "%H:%M").time()
            except Exception:
                message = "Format tanggal / waktu tidak valid."
            else:
                service = ServiceDB.query.filter_by(name=service_name).first()
                if not service:
                    message = "Layanan tidak ditemukan."
                else:
                    booking = BookingDB(
                        customer_id=user_id,
                        date=date_obj,
                        time=time_obj,
                        service_id=service.id,
                        note=note or "",
                        status="Menunggu Konfirmasi"
                    )
                    db.session.add(booking)
                    db.session.flush()
                    for item in cart:
                        name = item.get("name")
                        qty = int(item.get("qty", 0) or 0)
                        if not name or qty <= 0:
                            continue
                        spare = SparepartDB.query.filter_by(name=name).first()
                        if not spare:
                            continue
                        db.session.add(BookingItemDB(
                            booking_id=booking.id,
                            sparepart_id=spare.id,
                            qty=qty
                        ))
                    db.session.commit()
                    message = "Booking berhasil dikirim. Mohon menunggu konfirmasi dari admin."
    return render_template(
        "customer/customer_booking.html",
        customer_name=full_name,
        services=services,
        spareparts=spareparts,
        message=message
    )


@app.route("/customer/bookings/history")
def customer_booking_history():
    if session.get("role") != "customer":
        return redirect(url_for("login"))
    user_id = session.get("user_id")
    user = UserDB.query.get(user_id)
    full_name = user.full_name if user and user.full_name else (user.username if user else "Customer")
    bookings = BookingDB.query.filter_by(customer_id=user_id) \
                              .order_by(BookingDB.date.desc(), BookingDB.time.desc()) \
                              .all()
    return render_template(
        "customer/customer_booking_history.html",
        customer_name=full_name,
        bookings=bookings
    )

if __name__ == "__main__":
    # jalankan server saja, tanpa create_all setiap start
    app.run(debug=True)