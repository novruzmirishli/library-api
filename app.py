import os
import sqlite3
from datetime import date, timedelta
from rules import calculate_fine
from validators import check
from notifications import notify, write_notification
from database import init_db, find_book

from flask import Flask, jsonify, request

app = Flask(__name__)

# ---------- helpers ----------

def money(x):
    return round(float(x), 2)

# ---------- books ----------


@app.route("/books", methods=["GET"])
def list_books():
    conn = sqlite3.connect("library.db")
    if request.args.get("available") == "true":
        rows = conn.execute("SELECT * FROM books WHERE available = 1").fetchall()
    else:
        rows = conn.execute("SELECT * FROM books").fetchall()
    conn.close()
    result = []
    for r in rows:
        result.append({"id": r[0], "title": r[1], "author": r[2], "isbn": r[3], "available": bool(r[4])})
    return jsonify(result)


@app.route("/books/<int:book_id>", methods=["GET"])
def get_book(book_id):
    r = find_book(book_id)

    if r is None:
        return jsonify({"error": "book not found"}), 404

    return jsonify({
        "id": r[0],
        "title": r[1],
        "author": r[2],
        "isbn": r[3],
        "available": bool(r[4])
    })

@app.route("/books", methods=["POST"])
def create_book():
    data = request.get_json(silent=True) or {}
    if not data.get("title") or not data.get("author"):
        return jsonify({"error": "title and author are required"}), 400
    if not check(data.get("isbn"), "isbn"):
        return jsonify({"error": "invalid isbn"}), 400
    conn = sqlite3.connect("library.db")
    cur = conn.execute(
        "INSERT INTO books (title, author, isbn, available) VALUES (?, ?, ?, 1)",
        (data["title"], data["author"], data["isbn"]),
    )
    conn.commit()
    r = conn.execute("SELECT * FROM books WHERE id = ?", (cur.lastrowid,)).fetchone()
    conn.close()
    return jsonify({"id": r[0], "title": r[1], "author": r[2], "isbn": r[3], "available": bool(r[4])}), 201


@app.route("/books/<int:book_id>", methods=["DELETE"])
def delete_book(book_id):
    conn = sqlite3.connect("library.db")
    r = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    if r is None:
        conn.close()
        return jsonify({"error": "book not found"}), 404
    if r[4] == 0:
        conn.close()
        return jsonify({"error": "book is on loan"}), 409
    conn.execute("DELETE FROM books WHERE id = ?", (book_id,))
    conn.commit()
    conn.close()
    return "", 204


# ---------- members ----------


@app.route("/members", methods=["GET"])
def list_members():
    conn = sqlite3.connect("library.db")
    rows = conn.execute("SELECT * FROM members").fetchall()
    conn.close()
    result = []
    for r in rows:
        result.append({"id": r[0], "name": r[1], "email": r[2], "type": r[3]})
    return jsonify(result)


@app.route("/members/<int:member_id>", methods=["GET"])
def get_member(member_id):
    conn = sqlite3.connect("library.db")
    r = conn.execute("SELECT * FROM members WHERE id = ?", (member_id,)).fetchone()
    conn.close()
    if r is None:
        return jsonify({"error": "member not found"}), 404
    return jsonify({"id": r[0], "name": r[1], "email": r[2], "type": r[3]})


@app.route("/members", methods=["POST"])
def create_member():
    data = request.get_json(silent=True) or {}
    if not data.get("name"):
        return jsonify({"error": "name is required"}), 400
    if not check(data.get("email"), "email"):
        return jsonify({"error": "invalid email"}), 400
    member_type = data.get("type", "student")
    if not check(member_type, "member_type"):
        return jsonify({"error": "type must be student or staff"}), 400
    conn = sqlite3.connect("library.db")
    existing = conn.execute("SELECT * FROM members WHERE email = ?", (data["email"],)).fetchone()
    if existing is not None:
        conn.close()
        return jsonify({"error": "email already registered"}), 409
    cur = conn.execute(
        "INSERT INTO members (name, email, type) VALUES (?, ?, ?)",
        (data["name"], data["email"], member_type),
    )
    conn.commit()
    r = conn.execute("SELECT * FROM members WHERE id = ?", (cur.lastrowid,)).fetchone()
    conn.close()
    return jsonify({"id": r[0], "name": r[1], "email": r[2], "type": r[3]}), 201


@app.route("/members/<int:member_id>/loans", methods=["GET"])
def member_loans(member_id):
    conn = sqlite3.connect("library.db")
    m = conn.execute("SELECT * FROM members WHERE id = ?", (member_id,)).fetchone()
    if m is None:
        conn.close()
        return jsonify({"error": "member not found"}), 404
    rows = conn.execute("SELECT * FROM loans WHERE member_id = ?", (member_id,)).fetchall()
    conn.close()
    owed = 0
    open_loans = 0
    loans = []
    for loan in rows:
        if loan[4] == 0:
            open_loans += 1
            days_late = (date.today() - date.fromisoformat(loan[3])).days
            if days_late > 0:
                owed += calculate_fine(days_late)
        elif loan[6] == 0:
            owed += loan[5]
        loans.append(
            {
                "id": loan[0],
                "book_id": loan[1],
                "due_date": loan[3],
                "returned": bool(loan[4]),
                "fine": money(loan[5]),
                "fine_paid": bool(loan[6]),
            }
        )
    if m[3] == "staff":
        remaining = 5 - open_loans
    else:
        remaining = 3 - open_loans
    return jsonify({"loans": loans, "total_owed": money(owed), "loans_remaining": remaining})


# ---------- loans ----------


@app.route("/loans", methods=["GET"])
def list_loans():
    conn = sqlite3.connect("library.db")
    rows = conn.execute("SELECT * FROM loans").fetchall()
    conn.close()
    result = []
    for loan in rows:
        current_fine = loan[5]
        days_late = (date.today() - date.fromisoformat(loan[3])).days
        if loan[4] == 0:
            current_fine = calculate_fine(days_late)
        if request.args.get("overdue") == "true" and (loan[4] == 1 or days_late <= 0):
            continue
        result.append(
            {
                "id": loan[0],
                "book_id": loan[1],
                "member_id": loan[2],
                "due_date": loan[3],
                "returned": bool(loan[4]),
                "fine": money(loan[5]),
                "fine_paid": bool(loan[6]),
                "renewals": loan[7],
                "checked_out": loan[8],
                "current_fine": money(current_fine),
            }
        )
    return jsonify(result)


@app.route("/loans/<int:loan_id>", methods=["GET"])
def get_loan(loan_id):
    conn = sqlite3.connect("library.db")
    loan = conn.execute("SELECT * FROM loans WHERE id = ?", (loan_id,)).fetchone()
    conn.close()
    if loan is None:
        return jsonify({"error": "loan not found"}), 404
    current_fine = loan[5]
    if loan[4] == 0:
        days_late = (date.today() - date.fromisoformat(loan[3])).days
        current_fine = calculate_fine(days_late)
    return jsonify(
        {
            "id": loan[0],
            "book_id": loan[1],
            "member_id": loan[2],
            "due_date": loan[3],
            "returned": bool(loan[4]),
            "fine": money(loan[5]),
            "fine_paid": bool(loan[6]),
            "renewals": loan[7],
            "checked_out": loan[8],
            "current_fine": money(current_fine),
        }
    )


@app.route("/loans", methods=["POST"])
def create_loan():
    data = request.get_json(silent=True) or {}
    if not data.get("book_id") or not data.get("member_id"):
        return jsonify({"error": "book_id and member_id are required"}), 400
    if data.get("due_date") and not check(data["due_date"], "date"):
        return jsonify({"error": "invalid due_date"}), 400

    conn = sqlite3.connect("library.db")
    book = conn.execute("SELECT * FROM books WHERE id = ?", (data["book_id"],)).fetchone()
    if book is None:
        conn.close()
        return jsonify({"error": "book not found"}), 404
    member = conn.execute("SELECT * FROM members WHERE id = ?", (data["member_id"],)).fetchone()
    if member is None:
        conn.close()
        return jsonify({"error": "member not found"}), 404
    if book[4] == 0:
        conn.close()
        return jsonify({"error": "book is not available"}), 409

    rows = conn.execute("SELECT * FROM loans WHERE member_id = ?", (data["member_id"],)).fetchall()
    owed = 0
    open_loans = 0
    for loan in rows:
        if loan[4] == 0:
            open_loans += 1
            days_late = (date.today() - date.fromisoformat(loan[3])).days
            if days_late > 0:
                owed += calculate_fine(days_late)
        elif loan[6] == 0:
            owed += loan[5]
    if owed > 0:
        conn.close()
        return jsonify({"error": "member has unpaid fines"}), 409
    limit = 3
    if member[3] == "staff":
        limit = 5
    if open_loans >= limit:
        conn.close()
        return jsonify({"error": "loan limit reached"}), 409

    today = date.today()
    due_date = data.get("due_date") or (today + timedelta(days=14)).isoformat()
    cur = conn.execute(
        "INSERT INTO loans (book_id, member_id, due_date, checked_out) VALUES (?, ?, ?, ?)",
        (data["book_id"], data["member_id"], due_date, today.isoformat()),
    )
    conn.execute("UPDATE books SET available = 0 WHERE id = ?", (data["book_id"],))
    conn.commit()
    loan = conn.execute("SELECT * FROM loans WHERE id = ?", (cur.lastrowid,)).fetchone()
    conn.close()

    notify(member[2], "checkout", book[1])

    return (
        jsonify(
            {
                "id": loan[0],
                "book_id": loan[1],
                "member_id": loan[2],
                "due_date": loan[3],
                "returned": bool(loan[4]),
                "fine": money(loan[5]),
                "fine_paid": bool(loan[6]),
                "renewals": loan[7],
                "checked_out": loan[8],
            }
        ),
        201,
    )


@app.route("/loans/<int:loan_id>/return", methods=["POST"])
def return_book(loan_id):
    conn = sqlite3.connect("library.db")
    loan = conn.execute("SELECT * FROM loans WHERE id = ?", (loan_id,)).fetchone()
    if loan is None:
        conn.close()
        return jsonify({"error": "loan not found"}), 404
    if loan[4] == 1:
        conn.close()
        return jsonify({"error": "loan already returned"}), 409

    days_late = (date.today() - date.fromisoformat(loan[3])).days
    fine = calculate_fine(days_late)
    conn.execute("UPDATE loans SET returned = 1, fine = ? WHERE id = ?", (fine, loan_id))
    conn.execute("UPDATE books SET available = 1 WHERE id = ?", (loan[1],))
    conn.commit()
    member = conn.execute("SELECT * FROM members WHERE id = ?", (loan[2],)).fetchone()
    book = conn.execute("SELECT * FROM books WHERE id = ?", (loan[1],)).fetchone()
    updated = conn.execute("SELECT * FROM loans WHERE id = ?", (loan_id,)).fetchone()
    conn.close()

    write_notification(
        member[2],
        "Book returned",
        "You returned " + book[1] + ". Fine: $" + str(money(fine))
    )


    return jsonify(
        {
            "id": updated[0],
            "book_id": updated[1],
            "member_id": updated[2],
            "due_date": updated[3],
            "returned": bool(updated[4]),
            "fine": money(updated[5]),
            "fine_paid": bool(updated[6]),
            "renewals": updated[7],
            "checked_out": updated[8],
        }
    )


@app.route("/loans/<int:loan_id>/renew", methods=["POST"])
def renew_loan(loan_id):
    conn = sqlite3.connect("library.db")
    loan = conn.execute("SELECT * FROM loans WHERE id = ?", (loan_id,)).fetchone()
    if loan is None:
        conn.close()
        return jsonify({"error": "loan not found"}), 404
    if loan[4] == 1:
        conn.close()
        return jsonify({"error": "loan already returned"}), 409
    if date.fromisoformat(loan[3]) < date.today():
        conn.close()
        return jsonify({"error": "overdue loans cannot be renewed"}), 409
    if loan[7] >= 1:
        conn.close()
        return jsonify({"error": "loan was already renewed"}), 409

    new_due = (date.fromisoformat(loan[3]) + timedelta(days=14)).isoformat()
    conn.execute("UPDATE loans SET due_date = ?, renewals = renewals + 1 WHERE id = ?", (new_due, loan_id))
    conn.commit()
    member = conn.execute("SELECT * FROM members WHERE id = ?", (loan[2],)).fetchone()
    book = conn.execute("SELECT * FROM books WHERE id = ?", (loan[1],)).fetchone()
    updated = conn.execute("SELECT * FROM loans WHERE id = ?", (loan_id,)).fetchone()
    conn.close()

    notify(member[2], "renew", book[1])

    return jsonify(
        {
            "id": updated[0],
            "book_id": updated[1],
            "member_id": updated[2],
            "due_date": updated[3],
            "returned": bool(updated[4]),
            "fine": money(updated[5]),
            "fine_paid": bool(updated[6]),
            "renewals": updated[7],
            "checked_out": updated[8],
        }
    )


@app.route("/loans/<int:loan_id>/pay-fine", methods=["POST"])
def pay_fine(loan_id):
    conn = sqlite3.connect("library.db")
    loan = conn.execute("SELECT * FROM loans WHERE id = ?", (loan_id,)).fetchone()
    if loan is None:
        conn.close()
        return jsonify({"error": "loan not found"}), 404
    if loan[4] == 0:
        conn.close()
        return jsonify({"error": "return the book before paying"}), 409
    if loan[5] <= 0 or loan[6] == 1:
        conn.close()
        return jsonify({"error": "no fine to pay"}), 409

    conn.execute("UPDATE loans SET fine_paid = 1 WHERE id = ?", (loan_id,))
    conn.commit()
    member = conn.execute("SELECT * FROM members WHERE id = ?", (loan[2],)).fetchone()
    updated = conn.execute("SELECT * FROM loans WHERE id = ?", (loan_id,)).fetchone()
    conn.close()


   
    write_notification(
        member[2],
        "Fine paid",
        "We received $" + str(money(loan[5]))
    )


    return jsonify(
        {
            "id": updated[0],
            "book_id": updated[1],
            "member_id": updated[2],
            "due_date": updated[3],
            "returned": bool(updated[4]),
            "fine": money(updated[5]),
            "fine_paid": bool(updated[6]),
            "renewals": updated[7],
            "checked_out": updated[8],
        }
    )


# ---------- reports ----------


@app.route("/reports/overdue", methods=["GET"])
def report_overdue():
    conn = sqlite3.connect("library.db")
    rows = conn.execute(
        """
        SELECT loans.id, books.title, members.name, loans.due_date
        FROM loans
        JOIN books ON books.id = loans.book_id
        JOIN members ON members.id = loans.member_id
        WHERE loans.returned = 0
        """
    ).fetchall()
    conn.close()
    items = []
    total = 0
    for r in rows:
        days_late = (date.today() - date.fromisoformat(r[3])).days
        if days_late > 0:
            fine = calculate_fine(days_late)
            total += fine
            items.append(
                {"loan_id": r[0], "title": r[1], "member": r[2], "days_late": days_late, "fine": money(fine)}
            )
    return jsonify({"items": items, "total_fines": money(total)})


@app.route("/reports/summary", methods=["GET"])
def report_summary():
    conn = sqlite3.connect("library.db")
    total_books = conn.execute("SELECT COUNT(*) FROM books").fetchone()[0]
    rows = conn.execute("SELECT * FROM loans").fetchall()
    conn.close()
    on_loan = 0
    outstanding = 0
    for loan in rows:
        if loan[4] == 0:
            on_loan += 1
            days_late = (date.today() - date.fromisoformat(loan[3])).days
            outstanding += calculate_fine(days_late)
        elif loan[6] == 0:
            outstanding += loan[5]
    return jsonify({"total_books": total_books, "on_loan": on_loan, "fines_outstanding": money(outstanding)})


if not os.path.exists("library.db"):
    init_db()
